FROM freqtradeorg/freqtrade:2026.10@sha256:0cc4b6dc9b16e0516d27efae1765e925da7d6aea5c8f3c3825930ffe3098d7c7
WORKDIR /freqtrade
COPY --chown=ftuser:ftuser config.json /freqtrade/config.json
COPY --chown=ftuser:ftuser scripts /freqtrade/scripts
COPY --chown=ftuser:ftuser user_data/strategies /freqtrade/user_data/strategies
RUN mkdir -p /freqtrade/runtime
ENV PAPER_DATA_DIR=/freqtrade/runtime PAPER_BIND=0.0.0.0 PORT=8080 PYTHONUSERBASE=/home/ftuser/.local
HEALTHCHECK --interval=60s --timeout=90s --start-period=180s --retries=3 CMD python /freqtrade/scripts/healthcheck.py
USER root
ENTRYPOINT ["python", "/freqtrade/scripts/container_start.py"]
