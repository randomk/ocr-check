from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
import boto3
import os
from typing import List, Dict, Any
import json
from pydantic import BaseModel
import uvicorn
from tempfile import NamedTemporaryFile


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
            self.s3.put_object(
                Bucket=bucket,
                Key=filename,
                Body=file_content
            )
            return f"s3://{bucket}/{filename}"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro no upload para S3: {str(e)}")

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
            # Upload do arquivo
            self.upload_to_s3(file_content, filename, bucket)

            # Extrai texto
            extracted_text = self.extract_text_from_document(bucket, filename)

            # Procura nomes
            found_names = self.search_names(extracted_text, registered_names)

            # Remove o arquivo do S3 após processamento
            self.s3.delete_object(Bucket=bucket, Key=filename)

            return {
                'status': 'success',
                'text': extracted_text,
                'found_names': found_names
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


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

# Inicializa serviço OCR
ocr_service = OCRService(
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION', 'us-east-1')
)


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