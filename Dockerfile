FROM python:3.9-slim

WORKDIR /app

# Instala dependências do sistema e Rust
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    pkg-config \
    libssl-dev \
    git \
    && curl https://sh.rustup.rs -sSf | sh -s -- -y \
    && rm -rf /var/lib/apt/lists/*

# Adiciona cargo ao PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# Copia requirements e instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cria diretórios necessários
RUN mkdir -p templates static

# Copia arquivos da aplicação
COPY . .

# Define variáveis de ambiente
ENV AWS_ACCESS_KEY_ID=""
ENV AWS_SECRET_ACCESS_KEY=""
ENV AWS_REGION="us-east-1"
ENV S3_BUCKET=""
ENV PYTHONUNBUFFERED=1

# Expõe a porta
EXPOSE 8000

# Comando para executar
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]