"""Provider-selectable Streamlit GUI; all UI is rebuilt on every rerun."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from . import __version__
from .calibration import BUILTIN_SNAPSHOTS, builtin_bytes
from .config import PROVIDERS, Settings
from .data import from_dict, summary
from .errors import QuantivexError

SAMPLE_QUESTIONS = [
    "Which 4 qubits should I use for N2 at 1.1 Å, and can I trust the result?",
    "Does the choice of qubits matter for stretched N2 at 2.0 Å? Compare layouts.",
    "Plot the N2 dissociation curve from 0.8 to 2.4 Å on the best chain.",
    "Which qubits on this chip are unhealthy? Show me the chip map and a quantum-blurred noise map.",
]
LABELS = {
    "openai": "OpenAI",
    "anthropic": "Claude (Anthropic)",
    "ollama": "Ollama (local)",
    "local": "Local server (LM Studio / vLLM)",
    "openai-compatible": "Other OpenAI-compatible provider",
}


def main() -> None:
    st.set_page_config(page_title="Quantivex", layout="wide")
    if os.environ.get("QUANTIVEX_GUI_AUTH") == "oidc":
        if not st.user.is_logged_in:
            st.login()
            st.stop()
        allowed = {x.strip() for x in os.environ.get("QUANTIVEX_GUI_ALLOWED_EMAILS", "").split(",") if x.strip()}
        if not allowed or st.user.get("email") not in allowed or st.user.get("email_verified") is not True:
            st.error("This account is not authorized.")
            st.stop()
    st.title("Quantivex")
    st.caption(
        f"v{__version__} · Quantum-chemistry simulations and optional IBM Runtime execution. "
        "Tool results are authoritative; model explanations require review."
    )
    workspace = st.sidebar.radio("Workspace", ("Simulation assistant", "IBM execution"))
    if workspace == "IBM execution":
        from .ibm_gui import render

        render()
        return
    try:
        with st.sidebar:
            st.header("LLM provider")
            selected = os.environ.get("QUANTIVEX_PROVIDER", "openai")
            provider = st.selectbox(
                "Provider",
                PROVIDERS,
                index=PROVIDERS.index(selected) if selected in PROVIDERS else 0,
                format_func=lambda x: LABELS[x],
            )
            assert provider is not None
            defaults = Settings.from_env(validate=False, provider=provider)
            model = st.text_input("Model ID", defaults.model, key=f"model_{provider}")
            endpoint = st.text_input("API base URL", defaults.endpoint, key=f"endpoint_{provider}")
            key = st.text_input(
                "API key (optional for local servers)",
                "",
                type="password",
                key=f"key_{provider}",
                help="Enter a session key. Server credentials require OIDC authorization and are never prefilled.",
            )
            choice = st.selectbox(
                "First-turn tool choice",
                ("auto", "required"),
                index=0 if defaults.tool_choice == "auto" else 1,
                key=f"choice_{provider}",
            )
            st.caption(
                "Local mode connects only to your computer. Start a tool-capable model in your server first. "
                "Hosted providers receive your question and tool results."
            )
            st.header("Simulation")
            st.caption(f"Run output: {defaults.output_dir}")
            shots = st.number_input(
                "Shots per measurement group", min_value=100, max_value=100_000, value=defaults.shots, step=100
            )
            seed = st.number_input("Seed", min_value=0, max_value=2**31 - 1, value=defaults.seed, step=1)
            if (
                not key
                and defaults.api_key
                and os.environ.get("QUANTIVEX_GUI_AUTH") == "oidc"
                and endpoint.rstrip("/") != defaults.endpoint
            ):
                raise QuantivexError("Enter an explicit API key override when changing the endpoint")
            settings = Settings.from_env(
                validate=False,
                provider=provider,
                model=model,
                base_url=endpoint,
                api_key=key or (defaults.api_key if os.environ.get("QUANTIVEX_GUI_AUTH") == "oidc" else ""),
                tool_choice=choice,
                shots=int(shots),
                seed=int(seed),
            )
            if st.button("Check LLM compatibility"):
                from .providers import check_compatibility

                try:
                    with st.spinner("Two short model requests; API usage may be charged by your provider…"):
                        check_compatibility(settings.validated())
                    st.success("Tool-call round trip passed. Scientific judgement has not been certified.")
                except QuantivexError as exc:
                    st.error(str(exc))
    except QuantivexError as exc:
        st.error(f"Invalid configuration: {exc}")
        st.stop()

    source = st.radio("Calibration data", ("Bundled historical example", "Upload JSON"), horizontal=True)
    try:
        if source == "Bundled historical example":
            name = st.selectbox("Example", BUILTIN_SNAPSHOTS)
            assert name is not None
            raw_bytes = builtin_bytes(name)
            snapshot = from_dict(json.loads(raw_bytes))
            st.download_button("Download historical calibration JSON", raw_bytes, name, "application/json")
        else:
            upload = st.file_uploader("Calibration snapshot (schema v1 or v2)", type="json")
            if upload is None:
                st.stop()
            snapshot = from_dict(json.load(upload))
    except (QuantivexError, json.JSONDecodeError) as exc:
        st.error(f"Invalid data: {exc}")
        st.stop()

    info = summary(snapshot)
    cols = st.columns(5)
    cols[0].metric("Qubits", info["num_qubits"])
    cols[1].metric("Links", info["num_links"])
    cols[2].metric("Dead links", info["dead_links"])
    cols[3].metric("Median readout error", f"{100 * (info['median_readout_error'] or 0):.2f}%")
    cols[4].metric("Median T1", f"{info['median_t1_us']:.0f} µs" if info["median_t1_us"] else "n/a")
    st.caption(
        f"**{snapshot.label}** · calibration {snapshot.calibration_timestamp or 'unknown'} · "
        f"{snapshot.provenance} · SHA-256 {snapshot.sha256[:12]}…"
    )
    st.info("The bundled snapshot works offline without an IBM account. Results are simulations, not hardware runs.")

    preset = st.selectbox("Example questions", ["(write your own)", *SAMPLE_QUESTIONS]) or "(write your own)"
    question = st.text_area("Research question", "" if preset.startswith("(") else preset, height=80) or ""
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "data": snapshot.sha256,
                "question": question,
                "provider": provider,
                "model": model,
                "endpoint": endpoint,
                "shots": shots,
                "seed": seed,
                "choice": choice,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if st.button("Ask Quantivex", type="primary", disabled=not question.strip()):
        from .agent import ask

        st.session_state.pop("result", None)
        st.session_state.pop("result_fingerprint", None)
        if not settings.api_key and settings.provider in ("openai", "anthropic", "openai-compatible"):
            st.error(
                "Paste a session API key in the sidebar. For safety the GUI does not use keys saved in the "
                "environment (such as OPENAI_API_KEY) unless OIDC sign-in is configured; the command line does."
            )
            st.stop()
        try:
            with st.spinner("The agent is planning and running local simulations…"):
                st.session_state["result"] = ask(snapshot, question, settings.validated())
            st.session_state["result_fingerprint"] = fingerprint
        except QuantivexError as exc:
            st.error(str(exc))
    result: Any = st.session_state.get("result")
    if result is None or st.session_state.get("result_fingerprint") != fingerprint:
        return
    st.subheader("Answer")
    if getattr(result, "summary", ""):
        st.markdown(result.summary)
        if result.unverified_numbers:
            st.warning(
                "The model's explanation contains numbers that match no computed output: "
                + ", ".join(result.unverified_numbers)
            )
        with st.expander("Computed evidence (raw tool outputs)"):
            st.markdown(result.evidence)
    else:
        st.markdown(result.answer)
    run_dir = Path(result.run_dir)
    for art in result.artifacts:
        st.image(str(run_dir / art["file"]), caption=art["caption"], use_container_width=True)
    with st.expander(f"Agent audit trail ({len(result.tool_calls)} tool calls)"):
        st.dataframe(
            [
                {k: v for k, v in t.items() if k != "arguments"} | {"arguments": json.dumps(t["arguments"])}
                for t in result.tool_calls
            ],
            use_container_width=True,
        )
    c1, c2 = st.columns(2)
    c1.download_button(
        "Download report (Markdown)",
        Path(result.report_path).read_text(encoding="utf-8"),
        "quantivex_report.md",
        "text/markdown",
    )
    c2.download_button(
        "Download full record (JSON)",
        Path(result.record_path).read_text(encoding="utf-8"),
        "quantivex_record.json",
        "application/json",
    )
    st.caption(f"Saved in {run_dir}")


if __name__ == "__main__":
    main()
