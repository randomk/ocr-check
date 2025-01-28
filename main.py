from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import boto3
import os
from typing import List, Dict, Any
import json
from pydantic import BaseModel
import uvicorn
from tempfile import NamedTemporaryFile
from dotenv import load_dotenv

load_dotenv()


class SearchRequest(BaseModel):
    names: List[str]


class OCRService:
    def __init__(self, aws_access_key_id: str, aws_secret_access_key: str, region_name: str):
        """
        Inicializa o serviço OCR com credenciais AWS
        """
        self.textract = boto3.client(
            'textract',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name
        )

        self.s3 = boto3.client(
            's3',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name
        )

    def upload_to_s3(self, file_content: bytes, filename: str, bucket: str) -> str:
        """
        Faz upload do arquivo para S3
        """
        try:
            print(f"Iniciando upload para S3: bucket={bucket}, filename={filename}")

            # Extrai apenas o nome do bucket do ARN se necessário
            bucket_name = bucket.split(":")[-1] if "arn:aws:s3" in bucket else bucket
            print(f"Usando bucket name: {bucket_name}")

            # Tenta listar o bucket para verificar acesso
            try:
                self.s3.head_bucket(Bucket=bucket_name)
                print("Bucket verificado com sucesso")
            except Exception as e:
                print(f"Erro ao verificar bucket: {str(e)}")
                raise Exception(f"Erro ao acessar bucket: {str(e)}")

            # Faz o upload
            try:
                self.s3.put_object(
                    Bucket=bucket_name,
                    Key=filename,
                    Body=file_content
                )
                print(f"Arquivo {filename} enviado com sucesso para {bucket_name}")
                return f"s3://{bucket_name}/{filename}"
            except Exception as e:
                print(f"Erro no upload do arquivo: {str(e)}")
                raise Exception(f"Erro no upload: {str(e)}")

        except Exception as e:
            error_msg = f"Erro no upload para S3: {str(e)}"
            print(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

    def extract_text_from_document(self, bucket: str, document_key: str) -> str:
        """
        Extrai texto do documento usando AWS Textract
        """
        try:
            response = self.textract.start_document_text_detection(
                DocumentLocation={
                    'S3Object': {
                        'Bucket': bucket,
                        'Name': document_key
                    }
                }
            )

            job_id = response['JobId']

            # Aguarda conclusão do job
            while True:
                response = self.textract.get_document_text_detection(JobId=job_id)
                status = response['JobStatus']

                if status in ['SUCCEEDED', 'FAILED']:
                    break

            if status == 'FAILED':
                raise HTTPException(status_code=500, detail='Falha na extração do texto')

            # Extrai todo o texto
            text = ""
            for item in response['Blocks']:
                if item['BlockType'] == 'LINE':
                    text += item['Text'] + "\n"

            return text
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro na extração do texto: {str(e)}")

    def extract_date_of_birth(self, text: str) -> str:
        """
        Extrai a data de nascimento do texto do RG
        Procura por padrões comuns de data no formato dd/mm/yyyy
        """
        import re
        from datetime import datetime

        # Padrões comuns para data de nascimento em RGs
        patterns = [
            r'nasc\w*[\s:]+(\d{2}/\d{2}/\d{4})',  # Nascimento: dd/mm/yyyy
            r'data\s+de\s+nascimento[\s:]+(\d{2}/\d{2}/\d{4})',  # Data de Nascimento: dd/mm/yyyy
            r'dt\s*nasc[\s:]+(\d{2}/\d{2}/\d{4})',  # Dt Nasc: dd/mm/yyyy
            r'(\d{2}/\d{2}/\d{4})'  # Qualquer data no formato dd/mm/yyyy
        ]

        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                return match.group(1)

        return None

    def calculate_age(self, date_of_birth: str) -> Dict[str, Any]:
        """
        Calcula a idade e verifica se é menor de idade
        """
        from datetime import datetime

        try:
            # Converte a string de data para objeto datetime
            dob = datetime.strptime(date_of_birth, '%d/%m/%Y')

            # Calcula a idade
            today = datetime.now()
            age = today.year - dob.year

            # Ajusta a idade se ainda não fez aniversário este ano
            if today.month < dob.month or (today.month == dob.month and today.day < dob.day):
                age -= 1

            return {
                "age": age,
                "is_underage": age < 18,
                "date_of_birth": date_of_birth
            }
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Erro ao calcular idade: {str(e)}"
            )

    def search_names(self, text: str, registered_names: List[str]) -> Dict[str, List[int]]:
        """
        Procura nomes registrados no texto extraído
        Retorna um dicionário com os nomes encontrados e suas posições
        """
        results = {}
        lines = text.split('\n')

        for name in registered_names:
            name = name.lower()
            positions = []

            for i, line in enumerate(lines):
                if name in line.lower():
                    positions.append(i + 1)

            if positions:
                results[name] = positions

        return results

    def process_document(self, file_content: bytes, filename: str, bucket: str, registered_names: List[str]) -> Dict[
        str, Any]:
        """
        Processa o documento completo
        """
        try:
            print(f"Iniciando processamento do documento {filename}")

            # Upload do arquivo
            try:
                self.upload_to_s3(file_content, filename, bucket)
                print("Arquivo enviado para S3 com sucesso")
            except Exception as e:
                print(f"Erro no upload para S3: {str(e)}")
                raise

            # Extrai texto
            try:
                extracted_text = self.extract_text_from_document(bucket, filename)
                print("Texto extraído com sucesso")
                print(f"Texto extraído: {extracted_text[:200]}...")  # Primeiros 200 caracteres
            except Exception as e:
                print(f"Erro na extração de texto: {str(e)}")
                raise

            # Procura nomes
            try:
                found_names = self.search_names(extracted_text, registered_names)
                print(f"Nomes encontrados: {found_names}")
            except Exception as e:
                print(f"Erro na busca de nomes: {str(e)}")
                raise

            # Extrai data de nascimento e calcula idade
            try:
                date_of_birth = self.extract_date_of_birth(extracted_text)
                if date_of_birth:
                    age_info = self.calculate_age(date_of_birth)
                    print(f"Informações de idade: {age_info}")
                else:
                    age_info = None
                    print("Data de nascimento não encontrada")
            except Exception as e:
                print(f"Erro no cálculo de idade: {str(e)}")
                age_info = None

            # Remove o arquivo do S3 após processamento
            try:
                self.s3.delete_object(Bucket=bucket, Key=filename)
                print("Arquivo removido do S3")
            except Exception as e:
                print(f"Erro ao remover arquivo do S3: {str(e)}")
                # Não levanta exceção aqui pois o processamento já foi concluído

            return {
                'status': 'success',
                'text': extracted_text,
                'found_names': found_names,
                'age_verification': age_info if age_info else {
                    'error': 'Data de nascimento não encontrada no documento'
                }
            }

        except Exception as e:
            print(f"Erro no processamento do documento: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Erro no processamento do documento: {str(e)}"
            )


# Configuração dos templates
templates = Jinja2Templates(directory="templates")

# Inicializa FastAPI
app = FastAPI(
    title="Serviço OCR AWS",
    description="API para extração de texto e busca de nomes em documentos usando AWS Textract",
    version="1.0.0"
)

# Configuração CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializa serviço OCR com verificação
aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
aws_region = os.getenv('AWS_REGION', 'us-east-1')

if not aws_access_key or not aws_secret_key:
    print("⚠️ Atenção: Credenciais AWS não configuradas!")

# Inicializa o serviço OCR
ocr_service = OCRService(
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name=aws_region
)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    Renderiza a página inicial com o formulário de upload
    """
    return templates.TemplateResponse("index.html", {"request": request})


# Rota de healthcheck
@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/process")
async def process_document(
        file: UploadFile = File(...),
        search_request: str = Form(...),
):
    """
    Processa um documento e busca nomes
    - file: Arquivo a ser processado (PDF, PNG, JPEG)
    - search_request: JSON string contendo lista de nomes para buscar
    """
    try:
        # Valida e processa o JSON de busca
        search_data = json.loads(search_request)
        if not isinstance(search_data.get('names'), list):
            raise HTTPException(status_code=400, detail="Campo 'names' deve ser uma lista")

        # Lê o conteúdo do arquivo
        file_content = await file.read()

        # Processa o documento
        result = ocr_service.process_document(
            file_content=file_content,
            filename=file.filename,
            bucket=os.getenv('S3_BUCKET'),
            registered_names=search_data['names']
        )

        return result

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON de busca inválido")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)