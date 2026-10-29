FROM apache/airflow:3.3.2-python3.14

COPY --chown=airflow:root . /opt/project

RUN sed '/^polars==/d; /^polars-runtime-/d' \
        "${HOME}/constraints.txt" > /opt/project/constraints.txt \
    && pip install --no-cache-dir \
        "apache-airflow==${AIRFLOW_VERSION}" \
        --constraint /opt/project/constraints.txt \
        /opt/project \
    && pip check