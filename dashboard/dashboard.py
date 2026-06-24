"""
Zepto Expense Analytics Dashboard
Reads from Google Sheets and visualizes spending patterns.
Deploy free on Streamlit Cloud: https://streamlit.io/cloud
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta


st.set_page_config(
    page_title="Zepto Expense Tracker",
    page_icon="🛒",
    layout="wide",
)


@st.cache_data(ttl=300)  # refresh every 5 minutes
def load_data(sheet_id: str) -> pd.DataFrame:
    """Load data from a published Google Sheet."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    df = pd.read_csv(url)
    df["Date"] = pd.to_datetime(df["Date"], format="%d-%m-%Y", dayfirst=True)
    df["Month"] = df["Date"].dt.to_period("M").astype(str)
    df["Week"] = df["Date"].dt.isocalendar().week.astype(int)
    df["Amount (₹)"] = pd.to_numeric(df["Amount (₹)"], errors="coerce")
    df["Person's Share (₹)"] = pd.to_numeric(df["Person's Share (₹)"], errors="coerce")
    return df


def main():
    st.title("🛒 Zepto Expense Tracker")
    st.caption("Auto-updated from your Zepto Split Bot")

    # Sheet ID from secrets or sidebar input
    sheet_id = st.secrets.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        sheet_id = st.sidebar.text_input("Google Sheet ID", help="The ID from your sheet URL")

    if not sheet_id:
        st.info("Enter your Google Sheet ID to get started. Find it in the sheet URL: docs.google.com/spreadsheets/d/**SHEET_ID**/edit")
        return

    try:
        df = load_data(sheet_id)
    except Exception as e:
        st.error(f"Couldn't load data: {e}\n\nMake sure the sheet is published to web (File → Share → Publish to web → CSV).")
        return

    if df.empty:
        st.warning("No data yet. Process some Zepto orders through the bot first!")
        return

    # ── Sidebar Filters ──
    st.sidebar.header("Filters")

    months = sorted(df["Month"].unique(), reverse=True)
    selected_months = st.sidebar.multiselect("Month", months, default=months[:3] if len(months) >= 3 else months)

    persons = sorted(df["Tagged To"].unique())
    selected_persons = st.sidebar.multiselect("Person", persons, default=persons)

    # Apply filters
    filtered = df[
        (df["Month"].isin(selected_months)) &
        (df["Tagged To"].isin(selected_persons))
    ]

    if filtered.empty:
        st.warning("No data for selected filters.")
        return

    # ── Top Metrics ──
    col1, col2, col3, col4 = st.columns(4)

    # Deduplicate shared items (they have 2 rows — one per person)
    deduped = filtered.drop_duplicates(subset=["Order ID", "Item"])
    total_spend = deduped["Amount (₹)"].sum()
    total_orders = filtered["Order ID"].nunique()
    avg_order = total_spend / total_orders if total_orders > 0 else 0

    # Per-person totals (using their share, not item amount)
    person_shares = filtered.groupby("Tagged To")["Person's Share (₹)"].sum()

    col1.metric("Total Spend", f"₹{total_spend:,.0f}")
    col2.metric("Orders", f"{total_orders}")
    col3.metric("Avg Order", f"₹{avg_order:,.0f}")

    if "Tanmay" in person_shares.index:
        col4.metric("Your Share", f"₹{person_shares.get('Tanmay', 0):,.0f}")

    # ── Per-Person Share Breakdown ──
    st.markdown("---")
    st.subheader("💰 Who's Spending What")

    person_cols = st.columns(len(person_shares))
    colors = {"Tanmay": "#4CAF50", "Kalash": "#2196F3", "Abhirag": "#9C27B0"}

    for i, (person, share) in enumerate(person_shares.items()):
        with person_cols[i]:
            pct = (share / total_spend * 100) if total_spend > 0 else 0
            st.metric(person, f"₹{share:,.0f}", f"{pct:.0f}% of total")

    # ── Monthly Trend ──
    st.markdown("---")
    st.subheader("📈 Monthly Spending Trend")

    monthly = filtered.groupby(["Month", "Tagged To"])["Person's Share (₹)"].sum().reset_index()
    fig_trend = px.bar(
        monthly, x="Month", y="Person's Share (₹)", color="Tagged To",
        barmode="group", color_discrete_map=colors,
        labels={"Person's Share (₹)": "Amount (₹)"},
    )
    fig_trend.update_layout(xaxis_title="", yaxis_title="₹", legend_title="")
    st.plotly_chart(fig_trend, use_container_width=True)

    # ── Category Breakdown ──
    st.markdown("---")
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("🍕 Spending by Category")
        cat_spend = filtered.groupby("Category")["Person's Share (₹)"].sum().sort_values(ascending=False).reset_index()
        fig_cat = px.pie(
            cat_spend, names="Category", values="Person's Share (₹)",
            hole=0.4,
        )
        fig_cat.update_traces(textinfo="label+percent", textposition="outside")
        st.plotly_chart(fig_cat, use_container_width=True)

    with col_right:
        st.subheader("🏷️ Category by Person")
        cat_person = filtered.groupby(["Category", "Tagged To"])["Person's Share (₹)"].sum().reset_index()
        fig_cat_person = px.bar(
            cat_person, x="Category", y="Person's Share (₹)", color="Tagged To",
            barmode="stack", color_discrete_map=colors,
            labels={"Person's Share (₹)": "₹"},
        )
        fig_cat_person.update_layout(xaxis_tickangle=-45, legend_title="")
        st.plotly_chart(fig_cat_person, use_container_width=True)

    # ── Shared vs Personal ──
    st.markdown("---")
    st.subheader("🔀 Shared vs Personal Spending")

    split_type = filtered.groupby(["Tagged To", "Split Type"])["Person's Share (₹)"].sum().reset_index()
    fig_split = px.bar(
        split_type, x="Tagged To", y="Person's Share (₹)", color="Split Type",
        barmode="stack",
        color_discrete_map={"Personal": "#FF7043", "Shared": "#42A5F5"},
        labels={"Person's Share (₹)": "₹"},
    )
    fig_split.update_layout(legend_title="")
    st.plotly_chart(fig_split, use_container_width=True)

    # ── Top Items ──
    st.markdown("---")
    st.subheader("🔝 Most Bought Items")

    top_items = (
        deduped.groupby("Item")
        .agg({"Amount (₹)": "sum", "Order ID": "nunique"})
        .rename(columns={"Order ID": "Times Bought", "Amount (₹)": "Total Spent (₹)"})
        .sort_values("Total Spent (₹)", ascending=False)
        .head(15)
        .reset_index()
    )
    st.dataframe(top_items, use_container_width=True, hide_index=True)

    # ── Junk Food Tracker ──
    st.markdown("---")
    st.subheader("🍟 Junk Food / Packaged Spending")

    junk = filtered[filtered["Category"] == "Packaged/Junk"]
    if not junk.empty:
        junk_monthly = junk.groupby(["Month", "Tagged To"])["Person's Share (₹)"].sum().reset_index()
        fig_junk = px.bar(
            junk_monthly, x="Month", y="Person's Share (₹)", color="Tagged To",
            barmode="group", color_discrete_map=colors,
            labels={"Person's Share (₹)": "₹"},
        )
        st.plotly_chart(fig_junk, use_container_width=True)

        total_junk = junk["Person's Share (₹)"].sum()
        junk_pct = (total_junk / total_spend * 100) if total_spend > 0 else 0
        if junk_pct > 20:
            st.warning(f"⚠️ Packaged/junk food is {junk_pct:.0f}% of your grocery spend. Consider cutting back!")
        else:
            st.success(f"✅ Packaged/junk food is only {junk_pct:.0f}% of your spend. Looking healthy!")
    else:
        st.success("No junk food purchases in this period! 💪")

    # ── Raw Data ──
    with st.expander("📋 Raw Data"):
        st.dataframe(filtered.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
