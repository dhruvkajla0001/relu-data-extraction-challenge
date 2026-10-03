import re
import json
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Relu Data Explorer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

DISNEY_CSV = ROOT / "Disney" / "disney_cruises_submission.csv"

INGREDIENTS_CSV = (
    ROOT
    / "IngredientsNetwork"
    / "ingredients_network_final.csv"
)

INGREDIENTS_CHECKPOINT = (
    ROOT
    / "IngredientsNetwork"
    / "ingredients_network_checkpoint.json"
)


# ============================================================
# CONSTANTS
# ============================================================

INGREDIENT_COLUMNS = [
    "Company Name",
    "Company Description",
    "Sales Markets",
    "Primary Business Activity",
    "Categories",
    "Events",
    "Address",
    "Email",
    "Telephone",
    "Website",
]


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>

    .stApp {
        background: #f7f9fc;
    }

    .main-title {
        font-size: 2.6rem;
        font-weight: 800;
        letter-spacing: -1.2px;
        margin-bottom: 0.15rem;
    }

    .subtitle {
        color: #64748b;
        font-size: 1rem;
        margin-bottom: 1.5rem;
    }

    .hero-box {
        background: linear-gradient(
            135deg,
            #ffffff 0%,
            #f8fafc 100%
        );
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 4px 18px rgba(15, 23, 42, 0.05);
    }

    .metric-card {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 18px;
        box-shadow: 0 3px 14px rgba(15, 23, 42, 0.05);
    }

    .metric-label {
        color: #64748b;
        font-size: 0.82rem;
        font-weight: 650;
    }

    .metric-value {
        font-size: 1.85rem;
        font-weight: 800;
        margin-top: 4px;
    }

    .section-title {
        font-size: 1.35rem;
        font-weight: 750;
        margin-top: 1rem;
        margin-bottom: 0.8rem;
    }

    .info-box {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 18px;
        box-shadow: 0 3px 12px rgba(15, 23, 42, 0.04);
    }

    .status-good {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        color: #166534;
        border-radius: 10px;
        padding: 10px 14px;
        margin-bottom: 12px;
    }

    .status-warning {
        background: #fffbeb;
        border: 1px solid #fde68a;
        color: #92400e;
        border-radius: 10px;
        padding: 10px 14px;
        margin-bottom: 12px;
    }

    .footer {
        text-align: center;
        color: #94a3b8;
        padding: 35px 0 10px 0;
        font-size: 0.82rem;
    }

    div[data-testid="stMetric"] {
        background: white;
        border: 1px solid #e2e8f0;
        padding: 15px;
        border-radius: 14px;
        box-shadow: 0 3px 12px rgba(15, 23, 42, 0.04);
    }

    .stDataFrame {
        border-radius: 12px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# GENERIC CLEANING HELPERS
# ============================================================

def clean_value(value):
    """Convert missing / placeholder values into clean strings."""

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value = str(value).strip()

    if value.lower() in {
        "none",
        "nan",
        "null",
        "n/a",
        "na",
        "<na>",
    }:
        return ""

    return value


def clean_dataframe(df):
    """Standardize dataframe values."""

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    df.columns = [
        str(column).strip()
        for column in df.columns
    ]

    for column in df.columns:
        df[column] = df[column].map(clean_value)

    return df


def safe_contains(series, query):
    """Safe case-insensitive search."""

    if not query:
        return pd.Series(
            True,
            index=series.index
        )

    return (
        series.astype(str)
        .str.contains(
            re.escape(query),
            case=False,
            na=False,
        )
    )


def category_contains(value, target):
    """Match pipe-separated categories exactly."""

    value = clean_value(value)

    if not value:
        return False

    categories = [
        item.strip().lower()
        for item in value.split("|")
        if item.strip()
    ]

    return target.strip().lower() in categories


# ============================================================
# DISNEY HELPERS
# ============================================================

def derive_departure_port(row):
    """
    Derive departure port from the cruise title when the API
    field is missing.

    Examples:
        "3-Night Cruise from Singapore"
        -> "Singapore"

        "7-Night Alaska Cruise from Vancouver"
        -> "Vancouver"
    """

    existing = clean_value(
        row.get("departure_port", "")
    )

    if existing:
        return existing

    candidates = [
        row.get("product_display_name", ""),
        row.get("product_name", ""),
    ]

    for candidate in candidates:

        candidate = clean_value(candidate)

        if not candidate:
            continue

        # Usually titles end with:
        # "... from Singapore"
        match = re.search(
            r"\bfrom\s+(.+?)\s*$",
            candidate,
            flags=re.IGNORECASE,
        )

        if match:
            port = match.group(1).strip()

            # Remove accidental trailing punctuation.
            port = port.rstrip(".,;:")

            if port:
                return port

    return ""


@st.cache_data
def load_disney():

    if not DISNEY_CSV.exists():
        return pd.DataFrame()

    try:

        df = pd.read_csv(
            DISNEY_CSV,
            low_memory=False,
        )

        df = clean_dataframe(df)

        if df.empty:
            return df

        # Derive missing departure ports.
        if "departure_port" not in df.columns:
            df["departure_port"] = ""

        df["departure_port"] = df.apply(
            derive_departure_port,
            axis=1,
        )

        # Convert boolean-like fields safely.
        for column in [
            "is_pacific",
            "is_holiday",
        ]:
            if column in df.columns:
                df[column] = (
                    df[column]
                    .astype(str)
                    .str.lower()
                    .isin([
                        "true",
                        "1",
                        "yes",
                    ])
                )

        return df

    except Exception as exc:

        st.error(
            f"Disney dataset could not be loaded: {exc}"
        )

        return pd.DataFrame()


# ============================================================
# INGREDIENTS CHECKPOINT EXTRACTION
# ============================================================

def extract_checkpoint_records(payload):
    """
    Handle several possible checkpoint structures.

    Supports:
      - {"results": {...}}
      - {"results": [...]}
      - {"data": [...]}
      - direct list
    """

    if isinstance(payload, list):
        candidates = payload

    elif isinstance(payload, dict):

        candidates = None

        for key in [
            "results",
            "data",
            "records",
            "companies",
            "profiles",
        ]:
            if key in payload:
                candidates = payload[key]
                break

        if candidates is None:
            candidates = payload

    else:
        return []

    records = []

    if isinstance(candidates, dict):

        for value in candidates.values():

            if isinstance(value, dict):

                # Sometimes a record is nested.
                if "Company Name" in value:
                    records.append(value)

                elif "data" in value and isinstance(
                    value["data"],
                    dict
                ):
                    records.append(value["data"])

    elif isinstance(candidates, list):

        for value in candidates:

            if isinstance(value, dict):

                if "Company Name" in value:
                    records.append(value)

                elif "data" in value and isinstance(
                    value["data"],
                    dict
                ):
                    records.append(value["data"])

    return records


# ============================================================
# INGREDIENTS LOADER
# ============================================================

@st.cache_data
def load_ingredients():

    # --------------------------------------------------------
    # IMPORTANT:
    # Use the structured checkpoint FIRST.
    #
    # The submitted CSV can contain malformed CSV quoting.
    # The checkpoint is the cleaner source for the dashboard.
    # --------------------------------------------------------

    if INGREDIENTS_CHECKPOINT.exists():

        try:

            with open(
                INGREDIENTS_CHECKPOINT,
                "r",
                encoding="utf-8",
            ) as file:

                payload = json.load(file)

            records = extract_checkpoint_records(
                payload
            )

            if records:

                df = pd.DataFrame(records)

                # Guarantee all required columns.
                for column in INGREDIENT_COLUMNS:

                    if column not in df.columns:
                        df[column] = ""

                df = df[INGREDIENT_COLUMNS]

                df = clean_dataframe(df)

                # Remove completely empty company rows.
                df = df[
                    df["Company Name"].str.strip() != ""
                ]

                # Remove duplicate company profiles.
                df = df.drop_duplicates(
                    subset=["Company Name"],
                    keep="first",
                )

                df = df.reset_index(drop=True)

                if not df.empty:
                    return df

        except Exception as exc:

            st.warning(
                "Structured checkpoint could not be loaded. "
                f"Using CSV fallback. Details: {exc}"
            )

    # --------------------------------------------------------
    # CSV fallback
    # --------------------------------------------------------

    if INGREDIENTS_CSV.exists():

        try:

            df = pd.read_csv(
                INGREDIENTS_CSV,
                engine="python",
                on_bad_lines="skip",
                dtype=str,
            )

            df = clean_dataframe(df)

            # Guarantee schema.
            for column in INGREDIENT_COLUMNS:

                if column not in df.columns:
                    df[column] = ""

            df = df[INGREDIENT_COLUMNS]

            df = df[
                df["Company Name"].str.strip() != ""
            ]

            df = df.drop_duplicates(
                subset=["Company Name"],
                keep="first",
            )

            return df.reset_index(drop=True)

        except Exception as exc:

            st.error(
                f"Ingredients dataset could not be loaded: {exc}"
            )

    return pd.DataFrame(
        columns=INGREDIENT_COLUMNS
    )


# ============================================================
# LOAD DATA
# ============================================================

disney = load_disney()
ingredients = load_ingredients()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown("## 📊 Data Explorer")

    st.caption(
        "Relu Consultancy • Data Extraction Challenge"
    )

    st.divider()

    page = st.radio(
        "Explore",
        [
            "🏠 Overview",
            "🚢 Disney Cruises",
            "🧪 Ingredients Network",
        ],
    )

    st.divider()

    st.markdown("### Dataset Status")

    if disney.empty:
        st.write("🚢 Disney: unavailable")
    else:
        st.write(
            f"🚢 Disney: **{len(disney):,} records**"
        )

    if ingredients.empty:
        st.write("🧪 Ingredients: unavailable")
    else:
        st.write(
            f"🧪 Ingredients: **{len(ingredients):,} companies**"
        )

    st.divider()

    st.caption(
        "Interactive exploration and export"
    )


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">Relu Data Explorer</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="subtitle">'
    "Interactive exploration of extracted Disney Cruise "
    "and Ingredients Network data"
    "</div>",
    unsafe_allow_html=True,
)


# ============================================================
# OVERVIEW
# ============================================================

if page == "🏠 Overview":

    st.markdown(
        """
        <div class="hero-box">
            <h2>Data Extraction Challenge</h2>
            <p>
                Explore, filter and export structured data
                collected from two source platforms.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.metric(
            "Disney Records",
            f"{len(disney):,}"
            if not disney.empty
            else "—",
        )

    with c2:

        unique_products = (
            disney["product_id"].nunique()
            if (
                not disney.empty
                and "product_id" in disney.columns
            )
            else 0
        )

        st.metric(
            "Disney Products",
            f"{unique_products:,}",
        )

    with c3:
        st.metric(
            "Company Profiles",
            f"{len(ingredients):,}"
            if not ingredients.empty
            else "—",
        )

    with c4:
        st.metric(
            "Source Datasets",
            "2",
        )

    st.markdown("### 📦 Dataset Summary")

    left, right = st.columns(2)

    with left:

        st.markdown(
            """
            <div class="info-box">
                <h3>🚢 Disney Cruises</h3>
                <p>
                    Structured cruise availability records
                    extracted through the Disney availability
                    workflow.
                </p>
                <ul>
                    <li>35 API pages processed</li>
                    <li>948 API-reported cruises</li>
                    <li>323 flattened records</li>
                    <li>Departure port enrichment</li>
                    <li>Search and filtering</li>
                    <li>CSV export</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:

        st.markdown(
            f"""
            <div class="info-box">
                <h3>🧪 Ingredients Network</h3>
                <p>
                    Structured company profiles extracted
                    from Ingredients Network.
                </p>
                <ul>
                    <li>{len(ingredients):,} company profiles loaded</li>
                    <li>Structured company fields</li>
                    <li>Category-based filtering</li>
                    <li>Full-text search</li>
                    <li>Company detail view</li>
                    <li>CSV export</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("### ⚙️ Technology")

    tech_cols = st.columns(6)

    for column, technology in zip(
        tech_cols,
        [
            "Python",
            "Playwright",
            "Pandas",
            "REST / API",
            "CSV / JSON",
            "Streamlit",
        ],
    ):

        with column:
            st.info(technology)


# ============================================================
# DISNEY CRUISES
# ============================================================

elif page == "🚢 Disney Cruises":

    if disney.empty:

        st.error(
            "Disney dataset could not be loaded."
        )

        st.stop()

    st.markdown("## 🚢 Disney Cruise Explorer")

    # --------------------------------------------------------
    # KPIs
    # --------------------------------------------------------

    total_records = len(disney)

    unique_products = (
        disney["product_id"].nunique()
        if "product_id" in disney.columns
        else 0
    )

    pacific = (
        int(disney["is_pacific"].sum())
        if "is_pacific" in disney.columns
        else 0
    )

    holiday = (
        int(disney["is_holiday"].sum())
        if "is_holiday" in disney.columns
        else 0
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Cruise Records",
        f"{total_records:,}",
    )

    c2.metric(
        "Unique Products",
        f"{unique_products:,}",
    )

    c3.metric(
        "Pacific",
        f"{pacific:,}",
    )

    c4.metric(
        "Holiday",
        f"{holiday:,}",
    )

    st.divider()

    # --------------------------------------------------------
    # FILTERS
    # --------------------------------------------------------

    st.markdown("### 🔎 Search & Filters")

    f1, f2, f3 = st.columns(3)

    with f1:

        search = st.text_input(
            "Search",
            placeholder=(
                "Ship, destination, cruise name..."
            ),
            key="disney_search",
        )

    with f2:

        if "destination" in disney.columns:

            destinations = sorted(
                [
                    value
                    for value in (
                        disney["destination"]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .unique()
                    )
                    if value
                ]
            )

            destination = st.selectbox(
                "Destination",
                ["All"] + destinations,
            )

        else:

            destination = "All"

    with f3:

        if "ship" in disney.columns:

            ships = sorted(
                [
                    value
                    for value in (
                        disney["ship"]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .unique()
                    )
                    if value
                ]
            )

            ship = st.selectbox(
                "Ship",
                ["All"] + ships,
            )

        else:

            ship = "All"

    # --------------------------------------------------------
    # FILTER DATA
    # --------------------------------------------------------

    filtered = disney.copy()

    if search:

        search_mask = pd.Series(
            False,
            index=filtered.index,
        )

        for column in filtered.columns:

            search_mask |= safe_contains(
                filtered[column],
                search,
            )

        filtered = filtered[search_mask]

    if (
        destination != "All"
        and "destination" in filtered.columns
    ):

        filtered = filtered[
            filtered["destination"]
            == destination
        ]

    if (
        ship != "All"
        and "ship" in filtered.columns
    ):

        filtered = filtered[
            filtered["ship"]
            == ship
        ]

    st.markdown(
        f"""
        <div class="status-good">
            Showing <strong>{len(filtered):,}</strong>
            of <strong>{len(disney):,}</strong>
            cruise records
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --------------------------------------------------------
    # TABLE
    # --------------------------------------------------------

    display_columns = [
        "product_display_name",
        "ship",
        "destination",
        "geo_area",
        "number_of_nights",
        "number_of_sailings",
        "departure_port",
    ]

    display_columns = [
        column
        for column in display_columns
        if column in filtered.columns
    ]

    st.dataframe(
        filtered[display_columns],
        use_container_width=True,
        height=500,
        hide_index=True,
    )

    # --------------------------------------------------------
    # EXPORT
    # --------------------------------------------------------

    st.markdown("### ⬇️ Export")

    disney_export = filtered.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "Download filtered Disney CSV",
        data=disney_export,
        file_name="filtered_disney_cruises.csv",
        mime="text/csv",
        use_container_width=False,
    )

    # --------------------------------------------------------
    # DETAILS
    # --------------------------------------------------------

    st.markdown("### 📋 Cruise Details")

    if not filtered.empty:

        options = filtered.index.tolist()

        selected_index = st.selectbox(
            "Select cruise",
            options,
            format_func=lambda index: clean_value(
                filtered.loc[index].get(
                    "product_display_name",
                    f"Record {index}",
                )
            ),
            key="disney_record",
        )

        selected = filtered.loc[selected_index]

        detail_left, detail_right = st.columns(2)

        for number, column in enumerate(
            filtered.columns
        ):

            value = clean_value(
                selected[column]
            )

            with (
                detail_left
                if number % 2 == 0
                else detail_right
            ):

                st.markdown(
                    f"**{column.replace('_', ' ').title()}**"
                )

                st.write(
                    value if value else "—"
                )


# ============================================================
# INGREDIENTS NETWORK
# ============================================================

elif page == "🧪 Ingredients Network":

    if ingredients.empty:

        st.error(
            "Ingredients Network dataset could not be loaded."
        )

        st.stop()

    st.markdown(
        "## 🧪 Ingredients Network Explorer"
    )

    # --------------------------------------------------------
    # CATEGORY COUNTS
    # --------------------------------------------------------

    categories = (
        ingredients["Categories"]
        if "Categories" in ingredients.columns
        else pd.Series("", index=ingredients.index)
    )

    herbs_count = sum(
        category_contains(
            value,
            "Herbs, Spices",
        )
        for value in categories
    )

    physical_count = sum(
        category_contains(
            value,
            "Physical Formats",
        )
        for value in categories
    )

    cognitive_count = sum(
        category_contains(
            value,
            "Cognitive & Mental Health",
        )
        for value in categories
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Company Profiles",
        f"{len(ingredients):,}",
    )

    c2.metric(
        "Herbs & Spices",
        f"{herbs_count:,}",
    )

    c3.metric(
        "Physical Formats",
        f"{physical_count:,}",
    )

    c4.metric(
        "Cognitive & Mental Health",
        f"{cognitive_count:,}",
    )

    st.divider()

    # --------------------------------------------------------
    # SEARCH + FILTERS
    # --------------------------------------------------------

    st.markdown(
        "### 🔎 Search & Filters"
    )

    f1, f2 = st.columns([2, 1])

    with f1:

        search = st.text_input(
            "Search company profiles",
            placeholder=(
                "Company, category, market, "
                "business activity..."
            ),
            key="ingredient_search",
        )

    with f2:

        all_categories = set()

        for value in categories:

            value = clean_value(value)

            for category in value.split("|"):

                category = category.strip()

                if category:
                    all_categories.add(category)

        selected_category = st.selectbox(
            "Category",
            ["All"] + sorted(all_categories),
            key="ingredient_category",
        )

    # --------------------------------------------------------
    # FILTER DATA
    # --------------------------------------------------------

    filtered = ingredients.copy()

    if search:

        search_mask = pd.Series(
            False,
            index=filtered.index,
        )

        for column in filtered.columns:

            search_mask |= safe_contains(
                filtered[column],
                search,
            )

        filtered = filtered[
            search_mask
        ]

    if selected_category != "All":

        filtered = filtered[
            filtered["Categories"]
            .apply(
                lambda value: category_contains(
                    value,
                    selected_category,
                )
            )
        ]

    st.markdown(
        f"""
        <div class="status-good">
            Showing <strong>{len(filtered):,}</strong>
            of <strong>{len(ingredients):,}</strong>
            company profiles
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --------------------------------------------------------
    # TABLE
    # --------------------------------------------------------

    display_columns = [
        "Company Name",
        "Company Description",
        "Sales Markets",
        "Primary Business Activity",
        "Categories",
        "Events",
        "Address",
        "Email",
        "Telephone",
        "Website",
    ]

    display_columns = [
        column
        for column in display_columns
        if column in filtered.columns
    ]

    st.dataframe(
        filtered[display_columns],
        use_container_width=True,
        height=520,
        hide_index=True,
    )

    # --------------------------------------------------------
    # EXPORT
    # --------------------------------------------------------

    st.markdown("### ⬇️ Export")

    ingredient_export = filtered[
        INGREDIENT_COLUMNS
    ].copy()

    # Guarantee clean export values.
    for column in ingredient_export.columns:
        ingredient_export[column] = (
            ingredient_export[column]
            .map(clean_value)
        )

    ingredient_csv = ingredient_export.to_csv(
        index=False,
        encoding="utf-8-sig",
    ).encode("utf-8-sig")

    st.download_button(
        "Download filtered Ingredients CSV",
        data=ingredient_csv,
        file_name="filtered_ingredients_network.csv",
        mime="text/csv",
        use_container_width=False,
    )

    # --------------------------------------------------------
    # COMPANY DETAILS
    # --------------------------------------------------------

    st.markdown(
        "### 🏢 Company Details"
    )

    if not filtered.empty:

        options = filtered.index.tolist()

        selected_index = st.selectbox(
            "Select company",
            options,
            format_func=lambda index: clean_value(
                filtered.loc[index].get(
                    "Company Name",
                    f"Company {index}",
                )
            ),
            key="ingredient_company",
        )

        selected = filtered.loc[
            selected_index
        ]

        st.markdown(
            f"#### {clean_value(selected.get('Company Name'))}"
        )

        detail_left, detail_right = st.columns(2)

        for number, column in enumerate(
            INGREDIENT_COLUMNS
        ):

            value = clean_value(
                selected.get(column, "")
            )

            with (
                detail_left
                if number % 2 == 0
                else detail_right
            ):

                st.markdown(
                    f"**{column}**"
                )

                if column == "Website" and value:

                    if (
                        value.startswith("http://")
                        or value.startswith("https://")
                    ):

                        st.markdown(
                            f"[Open website]({value})"
                        )

                    else:

                        st.write(value)

                else:

                    st.write(
                        value if value else "—"
                    )


# ============================================================
# FOOTER
# ============================================================

st.markdown(
    """
    <div class="footer">
        Built for the Relu Consultancy Data Extraction Engineer Challenge
        • Python • Playwright • Pandas • Streamlit
    </div>
    """,
    unsafe_allow_html=True,
)