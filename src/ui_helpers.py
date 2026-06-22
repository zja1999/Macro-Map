from __future__ import annotations

import os
from urllib.parse import quote, urlencode

import streamlit as st

GITHUB_REPO_URL = "https://github.com/zja1999/Macro-Map"
GITHUB_ISSUE_TEMPLATE = "nutrition-request.md"
CONTACT_EMAIL_ENV_VAR = "MACRO_MAP_CONTACT_EMAIL"
CONTACT_EMAIL_SECRET_KEY = "contact_email"


def inject_responsive_styles() -> None:
    """Tighten Streamlit/Folium layout on phones without changing desktop behavior much."""
    st.markdown(
        """
        <style>
            .block-container {
                max-width: 1500px;
                padding-top: 1.1rem;
                padding-bottom: 2rem;
            }

            h1 {
                margin-bottom: 0.15rem;
            }

            div[data-testid="stMetric"] {
                background: transparent;
                border: 1px solid rgba(49, 51, 63, 0.18);
                border-radius: 0.65rem;
                padding: 0.55rem 0.7rem;
            }

            div[data-testid="stDataFrame"] {
                width: 100%;
            }

            div[data-testid="stButton"] > button,
            div[data-testid="stDownloadButton"] > button,
            div[data-testid="stLinkButton"] > a {
                width: 100%;
            }

            .macro-chain-table {
                border: 1px solid rgba(250, 250, 250, 0.16);
                border-radius: 0.5rem;
                overflow: hidden;
                margin: 0.5rem 0 1rem 0;
            }

            .macro-chain-table table {
                width: 100%;
                border-collapse: collapse;
                font-size: 0.9rem;
            }

            .macro-chain-table th,
            .macro-chain-table td {
                border-bottom: 1px solid rgba(250, 250, 250, 0.12);
                padding: 0.55rem 0.6rem;
                text-align: left;
                vertical-align: middle;
            }

            .macro-chain-table th {
                color: rgba(250, 250, 250, 0.78);
                font-weight: 600;
                background: rgba(250, 250, 250, 0.03);
            }

            .macro-chain-table tr:last-child td {
                border-bottom: none;
            }

            .macro-chain-on-file {
                color: #2e9f57;
                font-weight: 700;
            }

            .macro-chain-missing,
            .macro-chain-missing a {
                color: #ff4b4b;
                font-weight: 700;
            }

            .macro-chain-email a {
                font-size: 0.85rem;
            }

            /* Streamlit's default mobile stacking can still leave awkward widths. */
            @media (max-width: 768px) {
                .block-container {
                    padding-left: 0.65rem;
                    padding-right: 0.65rem;
                    padding-top: 0.6rem;
                }

                h1 {
                    font-size: 1.75rem !important;
                    line-height: 1.15 !important;
                }

                h2, h3 {
                    font-size: 1.1rem !important;
                    line-height: 1.25 !important;
                }

                p, .stCaption, div[data-testid="stMarkdownContainer"] {
                    font-size: 0.92rem;
                }

                div[data-testid="stHorizontalBlock"] {
                    flex-direction: column !important;
                    gap: 0.85rem !important;
                }

                div[data-testid="column"] {
                    width: 100% !important;
                    min-width: 100% !important;
                    flex: 1 1 100% !important;
                }

                iframe[title="streamlit_folium.st_folium"] {
                    height: 430px !important;
                    min-height: 430px !important;
                    border-radius: 0.6rem;
                }

                .leaflet-control-container .leaflet-top.leaflet-right {
                    top: 0.35rem;
                    right: 0.35rem;
                }

                .leaflet-draw-toolbar a {
                    width: 34px !important;
                    height: 34px !important;
                    line-height: 34px !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def dataframe_height(row_count: int, *, min_height: int = 180, max_height: int = 420) -> int:
    """Return a reasonable dataframe height so tables do not dominate mobile screens."""
    if row_count <= 0:
        return min_height
    return max(min_height, min(max_height, 38 + row_count * 35))


def nutrition_request_message(chain: str) -> str:
    """Return the standard plain-text request body for a missing chain."""
    return (
        f"Chain requested from Macro Map: {chain}\n\n"
        "Please add a matching CSV in `data/nutrition/` using the standard nutrition format."
    )


def nutrition_request_url(chain: str) -> str:
    """Return a pre-filled GitHub issue URL for requesting nutrition data."""
    params = urlencode(
        {
            "template": GITHUB_ISSUE_TEMPLATE,
            "title": f"Nutrition data request: {chain}",
            "body": nutrition_request_message(chain),
        }
    )
    return f"{GITHUB_REPO_URL}/issues/new?{params}"


def configured_contact_email() -> str:
    """Return the optional non-GitHub request destination, if configured."""
    env_email = os.getenv(CONTACT_EMAIL_ENV_VAR, "").strip()
    if env_email:
        return env_email

    try:
        secret_email = str(st.secrets.get(CONTACT_EMAIL_SECRET_KEY, "")).strip()
    except Exception:
        secret_email = ""

    return secret_email


def nutrition_request_mailto_url(chain: str) -> str | None:
    """Return a pre-filled mailto draft URL when a contact email has been configured."""
    recipient = configured_contact_email()
    if not recipient:
        return None

    subject = quote(f"Macro Map nutrition request: {chain}", safe="")
    body = quote(nutrition_request_message(chain).replace("\n", "\r\n"), safe="")
    return f"mailto:{quote(recipient, safe='@.,+-_')}?subject={subject}&body={body}"


def render_missing_chain_request_panel(missing_chains: list[str]) -> None:
    """Render a request path for chains with no nutrition CSV yet.

    The active compact layout now puts request links directly in the chain table,
    but this helper remains for older layouts.
    """
    if not missing_chains:
        st.success("All chains in this selection have nutrition CSVs on file.")
        return

    with st.expander("Request missing nutrition data", expanded=False):
        st.caption(
            "Pick a missing chain and open a pre-filled request. The email option opens a draft; "
            "it does not send automatically."
        )
        selected_chain = st.selectbox("Missing chain", missing_chains, key="missing_chain_request_select")
        request_message = nutrition_request_message(selected_chain)

        github_col, email_col = st.columns(2)
        github_col.link_button("Open GitHub request", nutrition_request_url(selected_chain), width="stretch")

        email_url = nutrition_request_mailto_url(selected_chain)
        if email_url:
            email_col.link_button("Open email draft", email_url, width="stretch")
        else:
            email_col.caption("Email drafts can be enabled with `contact_email` in Streamlit secrets.")

        st.text_area(
            "Copyable request message",
            request_message,
            height=130,
            help="Use this if the user does not have GitHub or email drafts are not configured.",
        )
