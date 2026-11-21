# The evaluation server: the Flask application behind gunicorn, with the
# sereno_model package and the trained artifact of the sibling sereno-model
# repository (the build context named "model", see docker-compose.yml).
# Nothing is configured here: the settings of app/config.py come from the
# environment of the container.
FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# lightgbm loads the GNU OpenMP runtime, which the slim image lacks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# The layout of the host (two sibling folders), so that the default MODEL_PATH
# resolves to the artifact.
WORKDIR /srv/sereno-backend

COPY requirements.txt requirements-model.txt ./
RUN pip install -r requirements.txt -r requirements-model.txt

COPY --from=model pyproject.toml /srv/sereno-model/pyproject.toml
COPY --from=model src /srv/sereno-model/src
RUN pip install --no-deps /srv/sereno-model

ARG MODEL_VERSION=3.0.0
COPY --from=model artifacts/${MODEL_VERSION}/model.joblib /srv/sereno-model/artifacts/${MODEL_VERSION}/model.joblib

COPY app ./app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sereno
USER sereno

EXPOSE 5000

# One worker by default (WEB_CONCURRENCY changes it): each worker loads its
# own copy of the model. All interfaces of the container; compose publishes
# the port on the loopback address of the host only.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--threads", "8", "--access-logfile", "-", "--no-control-socket", "app:create_app()"]
