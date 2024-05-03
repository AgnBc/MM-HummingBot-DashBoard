import math
import streamlit as st
import plotly.graph_objects as go
from dotenv import load_dotenv
import json
import pandas as pd
import logging
from sqlalchemy.exc import OperationalError
import os

from data_viz.candles import PerformanceCandles
from data_viz.charts import ChartsBase
from utils.st_utils import initialize_st_page, download_csv_button
from utils.etl_performance import ETLPerformance
from data_viz.tracers import PerformancePlotlyTracer

load_dotenv()


def get_total_and_exit_levels(executors_with_orders: pd.DataFrame, executors: pd.DataFrame):
    exit_level = executors_with_orders[executors_with_orders["position"] == "OPEN"].groupby("executor_id")["position"].count()
    executors["exit_level"] = executors["id"].map(exit_level).fillna(0.0).astype(int)
    executors["total_levels"] = executors["config"].apply(lambda x: len(json.loads(x)["prices"]))
    return executors

def get_executors_with_orders(executors: pd.DataFrame, orders: pd.DataFrame):
    df = pd.DataFrame(executors['custom_info'].tolist(), index=executors['id'],
                         columns=["custom_info"]).reset_index()
    df["custom_info"] = df["custom_info"].apply(lambda x: json.loads(x))
    df["orders"] = df["custom_info"].apply(lambda x: x["order_ids"])
    df.rename(columns={"id": "executor_id"}, inplace=True)
    exploded_df = df.explode("orders").rename(columns={"orders": "order_id"})
    exec_with_orders = exploded_df.merge(orders, left_on="order_id", right_on="client_order_id", how="inner")
    exec_with_orders = exec_with_orders[exec_with_orders["last_status"].isin(["SellOrderCompleted", "BuyOrderCompleted"])]
    return exec_with_orders[["executor_id", "order_id", "last_status", "last_update_timestamp", "price", "amount", "position"]]


def format_duration(seconds):
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{int(days)}d {int(hours)}h {int(minutes)}m"


intervals = {
    "1m": 60,
    "3m": 60 * 3,
    "5m": 60 * 5,
    "15m": 60 * 15,
    "30m": 60 * 30,
    "1h": 60 * 60,
    "6h": 60 * 60 * 6,
    "1d": 60 * 60 * 24,
}


def custom_sort(row):
    if row['type'] == 'buy':
        return 0, -row['number']
    else:
        return 1, row['number']


initialize_st_page(title="DCA Performance", icon="🚀")
st.subheader("🔫 Data source")

try:
    etl = ETLPerformance(host="dashboard-db-1",
                         port=5432,
                         database=os.environ.get("POSTGRES_DB"),
                         user=os.environ.get("POSTGRES_USER"),
                         password=os.environ.get("POSTGRES_PASSWORD"))
    etl.test_connection()
except Exception as e:
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        host = st.text_input("Host", "localhost")
    with col2:
        port = st.number_input("Port", value=5480, step=1)
    with col3:
        db_name = st.text_input("DB Name", os.environ.get("POSTGRES_DB"))
    with col4:
        db_user = st.text_input("DB User", os.environ.get("POSTGRES_USER"))
    with col5:
        db_password = st.text_input("DB Password", os.environ.get("POSTGRES_PASSWORD"), type="password")
    try:
        etl = ETLPerformance(host=host, port=port, database=db_name, user=db_user, password=db_password)
        st.success("Connected to PostgreSQL database successfully!")
    except OperationalError as e:
        # Log the error message to Streamlit interface
        st.error(f"Error connecting to PostgreSQL database: {e}")
        # Log the error to the console or log file
        logging.error(f"Error connecting to PostgreSQL database: {e}")
        st.stop()

executors = etl.read_executors()
market_data = etl.read_market_data()
orders = etl.read_orders()
executors_with_orders = get_executors_with_orders(executors, orders)
executors = get_total_and_exit_levels(executors_with_orders, executors)
charts = ChartsBase()
tracer = PerformancePlotlyTracer()

st.subheader("📊 Overview")
grouped_executors = executors.groupby(["instance", "controller_id", "exchange", "trading_pair", "db_name"]).agg(
    {"net_pnl_quote": "sum",
     "id": "count",
     "datetime": "min",
     "close_datetime": "max",
     "filled_amount_quote": "sum"}).reset_index()

# Apply the function to the duration column
grouped_executors["duration"] = (grouped_executors["close_datetime"] - grouped_executors["datetime"]).dt.total_seconds().apply(format_duration)
grouped_executors["filled_amount_quote"] = grouped_executors["filled_amount_quote"].apply(lambda x: f"$ {x:.2f}")
grouped_executors["net_pnl_quote"] = grouped_executors["net_pnl_quote"].apply(lambda x: f"$ {x:.2f}")
grouped_executors["filter"] = False
grouped_executors.rename(columns={"datetime": "start_datetime_utc",
                                  "id": "total_executors"}, inplace=True)
cols_to_show = ["filter", "controller_id", "exchange", "trading_pair", "db_name", "total_executors", "filled_amount_quote", "net_pnl_quote", "duration"]
selection = st.data_editor(grouped_executors[cols_to_show], use_container_width=True, hide_index=True, column_config={"filter": st.column_config.CheckboxColumn(required=True)})
with st.expander("🔍 Filters"):
    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
    with col1:
        db_name = st.multiselect("Select db", executors["db_name"].unique(), default=grouped_executors.loc[selection["filter"], "db_name"].unique())
    with col2:
        instance_name = st.multiselect("Select instance", executors["instance"].unique(), default=grouped_executors.loc[selection["filter"], "instance"].unique())
    with col3:
        controller_id = st.multiselect("Select controller", executors["controller_id"].unique(), default=grouped_executors.loc[selection["filter"], "controller_id"].unique())
    with col4:
        exchange = st.multiselect("Select exchange", executors["exchange"].unique(), default=grouped_executors.loc[selection["filter"], "exchange"].unique())
    with col5:
        trading_pair = st.multiselect("Select trading_pair", executors["trading_pair"].unique(), default=grouped_executors.loc[selection["filter"], "trading_pair"].unique())
    with col6:
        start_datetime = st.date_input("Start date", value=executors["datetime"].min())
    with col7:
        end_datetime = st.date_input("End date", value=executors["datetime"].max())

st.subheader("Performance Analysis")

filtered_executors_data = executors.copy()
if db_name:
    filtered_executors_data = filtered_executors_data[filtered_executors_data["db_name"].isin(db_name)]
if instance_name:
    filtered_executors_data = filtered_executors_data[filtered_executors_data["instance"].isin(instance_name)]
if controller_id:
    filtered_executors_data = filtered_executors_data[filtered_executors_data["controller_id"].isin(controller_id)]
if exchange:
    filtered_executors_data = filtered_executors_data[filtered_executors_data["exchange"].isin(exchange)]
if trading_pair:
    filtered_executors_data = filtered_executors_data[filtered_executors_data["trading_pair"].isin(trading_pair)]

# Apply datetime filters if start_datetime and end_datetime are not None
# if start_datetime:
#     filtered_executors_data = filtered_executors_data[filtered_executors_data["datetime"] >= pd.to_datetime(start_datetime)]
# if end_datetime:
#     filtered_executors_data = filtered_executors_data[filtered_executors_data["close_datetime"] <= pd.to_datetime(end_datetime)]

col1, col2, col3, col4, col5, col6, col7, col8, col9 = st.columns(9)
with col1:
    st.metric("Composed PnL", f"$ {filtered_executors_data['net_pnl_quote'].sum():.2f}")
    st.metric("Profit per Executor", f"$ {filtered_executors_data['net_pnl_quote'].sum() / len(filtered_executors_data):.2f}")
with col2:
    st.metric("Total Executors", f"{len(filtered_executors_data)}")
    st.metric("Total Volume", f"{filtered_executors_data['filled_amount_quote'].sum():.2f}")
with col3:
    st.metric("# Trailing Stop", f"{len(filtered_executors_data[filtered_executors_data['close_type'] == 'TRAILING_STOP'])}",
              delta=f"{filtered_executors_data[filtered_executors_data['close_type'] == 'TRAILING_STOP']['net_pnl_quote'].sum():.2f}")
with col4:
    st.metric("# Take Profit", f"{len(filtered_executors_data[filtered_executors_data['close_type'] == 'TAKE_PROFIT'])}",
              delta=f"{filtered_executors_data[filtered_executors_data['close_type'] == 'TAKE_PROFIT']['net_pnl_quote'].sum():.2f}")
with col5:
    st.metric("# Stop Loss", f"{len(filtered_executors_data[filtered_executors_data['close_type'] == 'STOP_LOSS'])}",
              delta=f"{filtered_executors_data[filtered_executors_data['close_type'] == 'STOP_LOSS']['net_pnl_quote'].sum():.2f}")
with col6:
    st.metric("# Early Stop", f"{len(filtered_executors_data[filtered_executors_data['close_type'] == 'EARLY_STOP'])}",
              delta=f"{filtered_executors_data[filtered_executors_data['close_type'] == 'EARLY_STOP']['net_pnl_quote'].sum():.2f}")
with col7:
    st.metric("# Time Limit", f"{len(filtered_executors_data[filtered_executors_data['close_type'] == 'TIME_LIMIT'])}",
              delta=f"{filtered_executors_data[filtered_executors_data['close_type'] == 'TIME_LIMIT']['net_pnl_quote'].sum():.2f}")
with col8:
    st.metric("Long %", f"{100 * len(filtered_executors_data[filtered_executors_data['side'] == 1]) / len(filtered_executors_data):.2f} %",
              delta=f"{filtered_executors_data[filtered_executors_data['side'] == 1]['net_pnl_quote'].sum():.2f}")
with col9:
    st.metric("Short %", f"{100 * len(filtered_executors_data[filtered_executors_data['side'] == 2]) / len(filtered_executors_data):.2f} %",
              delta=f"{filtered_executors_data[filtered_executors_data['side'] == 2]['net_pnl_quote'].sum():.2f}")

# PnL Over Time
realized_pnl_data = filtered_executors_data[["close_datetime", "net_pnl_quote"]].sort_values("close_datetime")
realized_pnl_data["cum_pnl_over_time"] = realized_pnl_data["net_pnl_quote"].cumsum()
st.plotly_chart(charts.realized_pnl_over_time(data=realized_pnl_data,
                                              cum_realized_pnl_column="cum_pnl_over_time"),
                use_container_width=True)

# Close Types
col1, col2, col3 = st.columns(3)
with col1:
    close_type_data = filtered_executors_data.groupby("close_type").agg({"id": "count"}).reset_index()
    st.plotly_chart(charts.close_types_pie_chart(data=close_type_data,
                                                 close_type_column="close_type",
                                                 values_column="id"), use_container_width=True)

# Level IDs
with col2:
    level_id_data = filtered_executors_data.groupby("level_id").agg({"id": "count"}).reset_index()
    st.plotly_chart(charts.level_id_pie_chart(level_id_data,
                                              level_id_column="level_id",
                                              values_column="id"),
                    use_container_width=True)

with (col3):
    intra_level_id_data = filtered_executors_data.groupby(['exit_level', 'close_type']).size().reset_index(name='count')
    fig = go.Figure()
    fig.add_trace(go.Pie(labels=intra_level_id_data.loc[intra_level_id_data["exit_level"] != 0, 'exit_level'],
                         values=intra_level_id_data.loc[intra_level_id_data["exit_level"] != 0, 'count'],
                         hole=0.4))
    fig.update_layout(title='Count of Close Types by Exit Level')
    st.plotly_chart(fig, use_container_width=True)

# Intra level Analysis
intra_level_id_pnl_data = filtered_executors_data.groupby(['exit_level'])['net_pnl_quote'].sum().reset_index(name='pnl')

fig = go.Figure()

for close_type in intra_level_id_data['close_type'].unique():
    temp_data = intra_level_id_data[intra_level_id_data['close_type'] == close_type]
    fig.add_trace(go.Bar(
        x=temp_data['exit_level'],
        y=temp_data['count'],
        name=close_type,
        yaxis='y'
    ))

fig.add_trace(go.Scatter(x=intra_level_id_pnl_data['exit_level'],
                         y=intra_level_id_pnl_data['pnl'],
                         mode='lines+markers',
                         name='PnL',
                         text=intra_level_id_pnl_data['pnl'].apply(lambda x: f"$ {x:.2f}"),
                         textposition='top center',
                         yaxis='y2'))

# Determine the maximum absolute value of count and pnl for setting the y-axis range
max_count = max(abs(intra_level_id_data['count'].min()), abs(intra_level_id_data['count'].max()))
max_pnl = max(abs(intra_level_id_pnl_data['pnl'].min()), abs(intra_level_id_pnl_data['pnl'].max()))

# Update layout
fig.update_layout(
    title='Count of Close Types by Exit Level and PnL by Exit Level',
    xaxis=dict(title='Exit Level'),
    yaxis=dict(title='Count', side='left', range=[-max_count, max_count]),
    yaxis2=dict(title='PnL', overlaying='y', side='right', range=[-max_pnl, max_pnl]),
    barmode='group'
)

st.plotly_chart(fig, use_container_width=True)



# Apply custom sorting function to create a new column 'sorting_order'
level_id_data[['type', 'number']] = level_id_data['level_id'].str.split('_', expand=True)
level_id_data["number"] = level_id_data["number"].astype(int)
level_id_data['sorting_order'] = level_id_data.apply(custom_sort, axis=1)
level_id_data = level_id_data.sort_values(by='sorting_order')
level_id_data.drop(columns=['type', 'number', 'sorting_order'], inplace=True)
st.plotly_chart(charts.level_id_histogram(level_id_data,
                                          level_id_column="level_id",
                                          values_column="id"),
                use_container_width=True)

# Market data
st.subheader("Market Data")
col1, col2, col3, col4 = st.columns(4)
with col1:
    trading_pair = st.selectbox("Select trading pair", market_data["trading_pair"].unique())
with col2:
    interval = st.selectbox("Select interval", list(intervals.keys()), index=3)
with col3:
    rows_per_page = st.number_input("Candles per Page", value=1500, min_value=1, max_value=5000)
filtered_market_data = market_data[market_data["trading_pair"] == trading_pair]
filtered_market_data.set_index("timestamp", inplace=True)
market_data_resampled = filtered_market_data.resample(f"{intervals[interval]}S").agg({
    "mid_price": "ohlc",
    "best_bid": "last",
    "best_ask": "last",
})
market_data_resampled.columns = market_data_resampled.columns.droplevel(0)

# Add pagination
total_rows = len(market_data_resampled)
total_pages = math.ceil(total_rows / rows_per_page)
if total_pages > 1:
    selected_page = st.select_slider("Select page", list(range(total_pages)), total_pages - 1, key="page_slider")
else:
    selected_page = 0
start_idx = selected_page * rows_per_page
end_idx = start_idx + rows_per_page
candles_df = market_data_resampled[start_idx:end_idx]
start_time_page = candles_df.index.min()
end_time_page = candles_df.index.max()
filtered_executors_data.sort_values("close_datetime", inplace=True)
filtered_executors_data["cum_net_pnl_quote"] = filtered_executors_data["net_pnl_quote"].cumsum()
filtered_executors_data["cum_filled_amount_quote"] = filtered_executors_data["filled_amount_quote"].cumsum()
page_filtered_executors_data = filtered_executors_data[(filtered_executors_data["datetime"] >= start_time_page) &
                                                       (filtered_executors_data["close_datetime"] <= end_time_page)]
performance_candles = PerformanceCandles(strategy_version="v2",
                                         rows=3,
                                         row_heights=[0.6, 0.2, 0.2],
                                         indicators_config=None,
                                         candles_df=candles_df,
                                         executors_df=page_filtered_executors_data,
                                         show_positions=True,
                                         show_buys=False,
                                         show_sells=False,
                                         show_pnl=False,
                                         show_quote_inventory_change=False,
                                         show_indicators=False,
                                         main_height=False,
                                         show_annotations=True)
candles_figure = performance_candles.figure()

candles_figure.add_trace(go.Scatter(x=page_filtered_executors_data["close_datetime"],
                                    y=page_filtered_executors_data["cum_net_pnl_quote"],
                                    mode="lines",
                                    fill="tozeroy",
                                    name="Cum Realized PnL (Quote)"), row=2, col=1)
candles_figure.add_trace(go.Scatter(x=page_filtered_executors_data["close_datetime"],
                                    y=page_filtered_executors_data["cum_filled_amount_quote"],
                                    mode="lines",
                                    fill="tozeroy",
                                    name="Cum Volume (Quote)"), row=3, col=1)
candles_figure.update_yaxes(title_text="Realized PnL ($)", row=2, col=1)
candles_figure.update_yaxes(title_text="Volume ($)", row=3, col=1)
st.plotly_chart(candles_figure, use_container_width=True)


# Tables section
st.divider()
st.subheader("Tables")
with st.expander("💵 Trades"):
    trade_fill = etl.read_trade_fill()
    st.write(trade_fill)
    download_csv_button(trade_fill, "trade_fill", "download-trades")
with st.expander("📩 Orders"):
    orders = etl.read_orders()
    st.write(orders)
    download_csv_button(orders, "orders", "download-orders")
if not market_data.empty:
    with st.expander("💱 Market Data"):
        st.write(market_data)
        download_csv_button(market_data, "market_data", "download-market-data")
with st.expander("📈 Executors"):
    st.write(executors)
    download_csv_button(executors, "executors", "download-executors")
