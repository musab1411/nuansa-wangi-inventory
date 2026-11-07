FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /node

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY branch_client.py test_scenarios.py ./

# Container cabang standby; dipakai lewat: docker compose exec branch-a python branch_client.py ...
CMD ["sh", "-c", "python branch_client.py ping; tail -f /dev/null"]
