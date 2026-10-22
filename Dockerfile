# Reproducible runtime (Linux, includes PySCF for off-grid geometries).
#   docker build -t quantivex .
#   docker run --rm -p 8501:8501 -e OPENAI_API_KEY=... quantivex              # GUI
#   docker run --rm -e OPENAI_API_KEY=... quantivex quantivex ask \
#       examples/ibm_torino_2025-02-26.json "Which qubits should I use for N2?"
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MPLBACKEND=Agg \
    QUANTIVEX_DATA_DIR=/data/quantivex

RUN useradd --create-home --uid 1000 quantivex
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[gui,ibm]"

COPY --chown=quantivex examples ./examples
COPY --chown=quantivex launch_gui.py ./
RUN mkdir -p /data/quantivex && chown -R quantivex:quantivex /data/quantivex
USER quantivex

VOLUME ["/data/quantivex"]

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1
CMD ["python", "-m", "streamlit", "run", "launch_gui.py", "--server.address=0.0.0.0", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
