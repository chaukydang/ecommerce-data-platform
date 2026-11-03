import streamlit as st
from google.cloud import bigquery
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os

# --- Configuration ---
PROJECT_ID = "clear-variety-492200-n0"
DATASET_ID = "olist_marts"
KEY_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", os.path.expanduser("~/.gcp/keyfile.json"))

@st.cache_resource
def get_bq_client():
    """Initializes BigQuery client."""
    return bigquery.Client.from_service_account_json(KEY_PATH)

client = get_bq_client()

st.set_page_config(page_title="Olist Executive Dashboard", layout="wide")

# --- Data Fetching ---

@st.cache_data
def get_orders_data():
    query = f"""
        SELECT 
            FORMAT_TIMESTAMP('%Y-%m', purchased_at) AS month,
            COUNT(DISTINCT order_id) AS total_orders,
            ROUND(SUM(total_order_revenue), 2) AS total_revenue
        FROM `{PROJECT_ID}.{DATASET_ID}.fct_orders`
        GROUP BY 1 ORDER BY 1
    """
    return client.query(query).to_dataframe()

@st.cache_data
def get_logistics_data():
    query = f"""
        SELECT 
            state,
            ROUND(AVG(days_to_delivery), 1) as avg_days
        FROM `{PROJECT_ID}.{DATASET_ID}.fct_delivery_by_state`
        WHERE days_to_delivery IS NOT NULL AND days_to_delivery >= 0
        GROUP BY 1 ORDER BY avg_days DESC
    """
    return client.query(query).to_dataframe()

@st.cache_data
def get_sellers_data():
    query = f"""
        SELECT 
            seller_id,
            ROUND(SUM(item_gross_revenue), 2) AS total_seller_revenue
        FROM `{PROJECT_ID}.{DATASET_ID}.fct_seller_revenue`
        GROUP BY 1 ORDER BY total_seller_revenue DESC LIMIT 10
    """
    return client.query(query).to_dataframe()

# --- Main Dashboard UI ---

st.title("📊 Olist Executive Dashboard")
st.markdown("---")
st.caption(f"Last refreshed: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M UTC')}")

try:
    df_orders = get_orders_data()
    df_log = get_logistics_data()
    df_sellers = get_sellers_data()

    # 1. KPI Summary
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Orders", f"{df_orders['total_orders'].sum():,}")
    m2.metric("Total Revenue", f"${df_orders['total_revenue'].sum() / 1e6:.2f}M")
    m3.metric("Avg Delivery Time", f"{df_log['avg_days'].mean():.1f} Days")

    # 2. Refined Monthly Performance Chart
    st.subheader("📈 Monthly Revenue & Order Volume")
    
    fig_orders = go.Figure()

    # Revenue Bars
    fig_orders.add_trace(go.Bar(
        x=df_orders['month'],
        y=df_orders['total_revenue'],
        name="Revenue",
        marker_color='#5dade2',
        hovertemplate="Revenue: $%{y:,.0f}<extra></extra>"
    ))

    # Order Volume Line
    fig_orders.add_trace(go.Scatter(
        x=df_orders['month'],
        y=df_orders['total_orders'],
        name="Orders",
        line=dict(color='#eb984e', width=3),
        yaxis="y2",
        hovertemplate="Orders: %{y:,}<extra></extra>"
    ))

    fig_orders.update_layout(
        template="plotly_white",
        height=450,
        margin=dict(t=30, b=20, l=20, r=20),
        hovermode="x unified",
        legend=dict(
            title_text="Metric",
            orientation="h", 
            yanchor="bottom", 
            y=1.02, 
            xanchor="right", 
            x=1
        ),
        
        # Primary Axis: Revenue
        yaxis=dict(
            title="Total Revenue ($)",
            tickprefix="$",
            showgrid=False,   # Horizontal grid removed
            zeroline=True,
            zerolinecolor="#f0f0f0",
            rangemode="tozero"
        ),
        
        # Secondary Axis: Orders
        yaxis2=dict(
            title="Order Volume",
            overlaying="y",
            side="right",
            showgrid=False,   # Horizontal grid removed
            zeroline=False,
            rangemode="tozero"
        ),
        xaxis=dict(
            title="Month",
            showgrid=False
        )
    )
    
    st.plotly_chart(fig_orders, use_container_width=True)

    # 3. Logistics and Sellers
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🚚 Avg Delivery Days by State")
        fig_log = px.bar(
            df_log, x='state', y='avg_days',
            color='avg_days', 
            color_continuous_scale='Reds', 
            labels={'avg_days': 'Average Days', 'state': 'State'}
        )
        fig_log.update_layout(template="plotly_white", showlegend=False)
        fig_log.update_yaxes(showgrid=False) # Horizontal grid removed
        st.plotly_chart(fig_log, use_container_width=True)

    with col2:
        st.subheader("🏆 Top 10 Sellers by Revenue")
        fig_sellers = px.bar(
            df_sellers, x='total_seller_revenue', y='seller_id',
            orientation='h', 
            color='total_seller_revenue', 
            color_continuous_scale='Viridis', 
            labels={'seller_id': 'Seller ID', 'total_seller_revenue': 'Total Revenue $'}
        )
        fig_sellers.update_layout(
            template="plotly_white", 
            yaxis={'categoryorder':'total ascending'}
        )
        fig_sellers.update_xaxes(showgrid=False) # Vertical grid removed for horizontal bars
        st.plotly_chart(fig_sellers, use_container_width=True)

except Exception as e:
    st.error("Error loading dashboard data.")
    st.exception(e)