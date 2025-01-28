FROM python:3.13-slim

# Diretório de trabalho
WORKDIR /app

# Instala dependências do sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia os arquivos de requisitos
COPY requirements.txt .

# Instala dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação e templates
COPY main.py .
COPY templates/ templates/
COPY static/ static/

# Define variáveis de ambiente padrão
ENV AWS_REGION="us-east-1"
ENV S3_BUCKET=""

# Expõe a porta da API
EXPOSE 8000

# Comando para executar a API com reload para desenvolvimento
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]