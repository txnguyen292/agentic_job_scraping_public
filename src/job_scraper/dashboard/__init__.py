from __future__ import annotations

import argparse

import pandas as pd
import streamlit as st

from job_scraper.db import crawl_history, ensure_db, job_metrics, query_jobs, source_health


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default="data/jobs.db")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    args = parse_args()
    conn = ensure_db(args.db)

    st.set_page_config(page_title="Job Scraper Dashboard", layout="wide")
    st.title("AI/ML Job Radar")
    st.caption("Thin SQLite dashboard over the local crawl pipeline.")

    metrics = job_metrics(conn)
    left, middle, right = st.columns(3)
    left.metric("Total Jobs", int(metrics["total_jobs"] or 0))
    middle.metric("Relevant Jobs", int(metrics["relevant_jobs"] or 0))
    right.metric("Last Scrape", metrics["last_scraped_at"] or "n/a")

    with st.sidebar:
        st.header("Filters")
        keyword = st.text_input("Keyword")
        min_score = st.slider("Minimum Score", 0.0, 1.0, 0.35, 0.05)
        relevant_only = st.checkbox("Relevant Only", value=True)
        limit = st.slider("Row Limit", 10, 500, 100, 10)

        health_rows = list(source_health(conn))
        source_names = ["All Sources"] + [row["source_name"] for row in health_rows]
        source_name = st.selectbox("Source", source_names)

    rows = query_jobs(
        conn,
        keyword=keyword,
        relevant_only=relevant_only,
        min_score=min_score,
        limit=limit,
        source_name="" if source_name == "All Sources" else source_name,
    )
    jobs_df = pd.DataFrame([dict(row) for row in rows])
    if jobs_df.empty:
        st.info("No jobs match the current filters.")
    else:
        st.subheader("Jobs")
        st.dataframe(
            jobs_df[
                [
                    "company_name",
                    "title",
                    "team",
                    "location_raw",
                    "remote_type",
                    "overall_score",
                    "ai_ml_score",
                    "startup_score",
                    "job_url",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Source Health")
    source_df = pd.DataFrame([dict(row) for row in source_health(conn)])
    if source_df.empty:
        st.info("No sources have been crawled yet.")
    else:
        st.dataframe(source_df, use_container_width=True, hide_index=True)

    st.subheader("Crawl History")
    history_df = pd.DataFrame([dict(row) for row in crawl_history(conn)])
    if history_df.empty:
        st.info("No crawl runs recorded yet.")
    else:
        st.dataframe(history_df, use_container_width=True, hide_index=True)

    conn.close()


main()
