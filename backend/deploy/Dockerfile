FROM mambaorg/micromamba:2.3.2

USER root
WORKDIR /app
ENV PATH="/opt/conda/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 与本地共用 Python 和 CPU 推理依赖，curl 供容器健康检查使用。
COPY environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml curl \
    && micromamba clean --all --yes
COPY backend/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY backend/ .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
