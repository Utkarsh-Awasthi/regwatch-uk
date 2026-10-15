FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY regwatch ./regwatch
COPY evals ./evals
ENV REGWATCH_DB=/data/regwatch.db REGWATCH_OUTBOX=/data/outbox
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "regwatch", "serve", "--host", "0.0.0.0", "--port", "8000"]
