FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY app /app/app
COPY README.md /app/README.md
RUN pip install --no-cache-dir .

EXPOSE 30182
CMD ["python", "-m", "app.main"]
