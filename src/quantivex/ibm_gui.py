"""Explicit IBM execution controls, independent of LLM configuration."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import streamlit as st

from . import ibm_execution as ibm
from .chemistry import get_problem
from .config import default_hardware_output_dir
from .errors import QuantivexError
from .ibm_import import fake_backend
from .vqe import run_vqe


def render() -> None:
    st.subheader("IBM execution")
    st.caption(
        "Measure a locally optimized state using IBM Runtime. No LLM is needed. "
        "The offline workspace needs no IBM account. "
        "Real hardware uses the local CLI with a saved account or IBM-specific environment credentials."
    )
    st.info("This GUI permits offline tests only. Use the local CLI for account-backed IBM operations.")
    st.caption(f"New offline job folders: {default_hardware_output_dir()}")
    local = True
    name = st.text_input("Backend name", "FakeManilaV2" if local else "", key=f"ibm_backend_{local}")
    molecule = st.selectbox("Molecule", ("H2", "N2", "LiH"))
    bond = st.number_input("Bond length (Å)", min_value=0.1, value=0.75, step=0.05)
    shots = st.number_input("Hardware shots per group", min_value=2, max_value=100_000, value=4000, step=100)
    mode = st.selectbox("Circuit mode", ("compact", "uccsd"))
    st.caption(
        "Optimization is noiseless and local. Hardware counts are unmitigated; sampling errors exclude bias/drift."
    )
    try:
        if st.button("Prepare job (no submission)", disabled=not name.strip()):
            directory = default_hardware_output_dir() / uuid4().hex
            with st.spinner("Optimizing locally and compiling for the selected backend…"):
                backend = fake_backend(name) if local else ibm.get_backend(name)
                ibm.prepare(
                    run_vqe(get_problem(str(molecule), float(bond))),
                    backend,
                    directory,
                    shots=int(shots),
                    mode=str(mode),
                )
            st.session_state["ibm_folder"] = str(directory)
        directory_text = st.text_input("Prepared job folder", key="ibm_folder")
        if not directory_text:
            return
        directory = Path(directory_text)
        plan, _, _ = ibm.load_plan(directory)
        if plan["execution"] != "local_test":
            raise QuantivexError("Account-backed job folders are available only through the local CLI")
        st.json(plan)
        st.info(
            f"Selected plan: {plan['execution']} on {plan['backend']}, {len(plan['groups'])} circuits, "
            f"{plan['total_shots']} total shots. No price estimate; consult your IBM plan/quota."
        )
        if plan["execution"] == "local_test":
            if st.button("Run this plan locally"):
                with st.spinner("Testing the Runtime interface on the fake backend…"):
                    st.json(ibm.run_local(directory, fake_backend(plan["backend"])))
        else:
            receipt_path = directory / "job.json"
            if not receipt_path.exists():
                accepted = st.checkbox(
                    f"I authorize this plan's {plan['total_shots']} shots on {plan['backend']}; "
                    "IBM quota/charges may apply",
                    key=f"ibm_consent_{plan['job_tag']}",
                )
                if st.button("Submit this plan to IBM", disabled=not accepted):
                    st.json(ibm.submit(directory, confirm=accepted, max_total_shots=plan["total_shots"]))
                    st.rerun()
            else:
                st.json(json.loads(receipt_path.read_text()))
                if st.button("Refresh IBM job / retrieve result"):
                    st.json(ibm.fetch(directory))
                st.caption(
                    "An unresolved submission can be recovered with the CLI's ibm recover command; do not resubmit."
                )
        if (directory / "result.json").exists():
            raw = (directory / "result.json").read_bytes()
            st.json(json.loads(raw))
            st.download_button("Download execution result", raw, "quantivex_execution.json", "application/json")
    except QuantivexError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - hide SDK transport/credential details
        st.error(f"IBM workflow failed ({type(exc).__name__}); check the job folder and IBM dashboard before retrying.")
