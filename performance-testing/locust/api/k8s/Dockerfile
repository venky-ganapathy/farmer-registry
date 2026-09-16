FROM python:3.12-slim
WORKDIR /app
# kubectl: SLO ramp polls `kubectl top` the same way laptop e2e does.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && pip install --no-cache-dir locust requests \
    && rm -rf /var/lib/apt/lists/*
# Seed stays at Desktop/openg2p/perf-seed (do not copy into locust/api).
#   docker build -f k8s/Dockerfile \
#     --build-context locust=. \
#     --build-context seed="$HOME/Desktop/openg2p/perf-seed" \
#     -t vin0dkhichar/locust-staff-soak:latest \
#     k8s
COPY --from=locust . /app
COPY --from=seed . /perf-seed
RUN chmod +x /app/in-cluster-run.sh
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PERF_SEED_DIR=/perf-seed
WORKDIR /app
