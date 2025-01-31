from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import boto3
import os
from typing import List, Dict, Any, AsyncGenerator
import json
import zipfile
from io import BytesIO
from datetime import datetime
import re
from dotenv import load_dotenv
import asyncio

# Carrega variáveis de ambiente
load_dotenv()

# Inicializa FastAPI
app = FastAPI(
    title="Serviço OCR AWS",
    description="API para extração de texto e verificação de idade em documentos",
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

# Configuração dos templates
templates = Jinja2Templates(directory="templates")


class OCRService:
    def __init__(self, aws_access_key_id: str, aws_secret_access_key: str, region_name: str):
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
        try:
            print(f"Iniciando upload para S3: bucket={bucket}, filename={filename}")
            bucket_name = bucket.split(":")[-1] if "arn:aws:s3" in bucket else bucket

            try:
                self.s3.head_bucket(Bucket=bucket_name)
            except Exception as e:
                print(f"Erro ao verificar bucket: {str(e)}")
                raise

            self.s3.put_object(
                Bucket=bucket_name,
                Key=filename,
                Body=file_content
            )
            return f"s3://{bucket_name}/{filename}"
        except Exception as e:
            print(f"Erro no upload para S3: {str(e)}")
            raise

    def extract_text_from_document(self, bucket: str, document_key: str) -> str:
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

            while True:
                response = self.textract.get_document_text_detection(JobId=job_id)
                status = response['JobStatus']
                if status in ['SUCCEEDED', 'FAILED']:
                    break

            if status == 'FAILED':
                raise HTTPException(status_code=500, detail='Falha na extração do texto')

            text = ""
            for item in response['Blocks']:
                if item['BlockType'] == 'LINE':
                    text += item['Text'] + "\n"

            return text
        except Exception as e:
            print(f"Erro na extração de texto: {str(e)}")
            raise

    def extract_date_of_birth(self, text: str) -> str:
        # Encontra todas as datas no formato dd/mm/yyyy
        pattern = r'\d{2}/\d{2}/\d{4}'
        dates = re.findall(pattern, text)

        print(f"Datas encontradas no documento: {dates}")

        if len(dates) >= 2:
            date_objects = []
            for date_str in dates:
                try:
                    date_obj = datetime.strptime(date_str, '%d/%m/%Y')
                    date_objects.append((date_str, date_obj))
                except ValueError:
                    continue

            if date_objects:
                date_objects.sort(key=lambda x: x[1])  # Ordena pela data
                oldest_date = date_objects[0][0]  # Pega a string da data mais antiga
                print(f"Data mais antiga (nascimento): {oldest_date}")
                return oldest_date

        print("Não foi possível encontrar duas datas válidas para comparação")
        return None

    def calculate_age(self, date_of_birth: str) -> Dict[str, Any]:
        try:
            dob = datetime.strptime(date_of_birth, '%d/%m/%Y')
            today = datetime.now()
            age = today.year - dob.year

            if today.month < dob.month or (today.month == dob.month and today.day < dob.day):
                age -= 1

            return {
                "age": age,
                "is_underage": age < 18,
                "date_of_birth": date_of_birth
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao calcular idade: {str(e)}")

    async def process_file(self, filename: str, content: bytes, bucket: str) -> Dict[str, Any]:
        try:
            # Upload para S3
            s3_path = self.upload_to_s3(content, filename, bucket)

            # Extrai texto
            extracted_text = self.extract_text_from_document(bucket, filename)

            # Procura data de nascimento
            date_of_birth = self.extract_date_of_birth(extracted_text)
            age_info = None
            if date_of_birth:
                age_info = self.calculate_age(date_of_birth)

            # Remove arquivo do S3
            try:
                self.s3.delete_object(Bucket=bucket, Key=filename)
            except Exception as e:
                print(f"Erro ao remover arquivo do S3: {str(e)}")

            return {
                'filename': filename,
                'text': extracted_text,
                'age_verification': age_info if age_info else {
                    'error': 'Data de nascimento não encontrada'
                }
            }
        except Exception as e:
            print(f"Erro ao processar arquivo {filename}: {str(e)}")
            return {
                'filename': filename,
                'error': str(e)
            }

    async def process_stream(self, file_content: bytes) -> AsyncGenerator[str, None]:
        try:
            zip_buffer = BytesIO(file_content)
            results = {
                'documents': [],
                'summary': {
                    'total_processed': 0,
                    'underage_count': 0,
                    'adult_count': 0,
                    'error_count': 0,
                    'underage_list': [],
                    'error_files': []
                }
            }

            with zipfile.ZipFile(zip_buffer) as zip_file:
                valid_files = [f for f in zip_file.namelist()
                               if f.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg'))]

                total_files = len(valid_files)
                processed = 0

                # Processa arquivos em lotes de 5
                batch_size = 5
                for i in range(0, len(valid_files), batch_size):
                    batch = valid_files[i:i + batch_size]

                    # Prepara o processamento em paralelo
                    tasks = []
                    for filename in batch:
                        file_content = zip_file.read(filename)
                        tasks.append(self.process_file(
                            filename=filename,
                            content=file_content,
                            bucket=os.getenv('S3_BUCKET')
                        ))

                    # Processa o lote em paralelo
                    batch_results = await asyncio.gather(*tasks)

                    # Processa os resultados do lote
                    for result in batch_results:
                        # Atualiza estatísticas
                        results['summary']['total_processed'] += 1

                        if 'error' in result:
                            results['summary']['error_count'] += 1
                            results['summary']['error_files'].append({
                                'filename': result['filename'],
                                'error': result['error']
                            })
                        elif result.get('age_verification'):
                            age_info = result['age_verification']
                            if age_info.get('is_underage'):
                                results['summary']['underage_count'] += 1
                                name = os.path.splitext(os.path.basename(result['filename']))[0]
                                results['summary']['underage_list'].append({
                                    'name': name,
                                    'age': age_info['age'],
                                    'date_of_birth': age_info['date_of_birth'],
                                    'filename': result['filename']
                                })
                            else:
                                results['summary']['adult_count'] += 1

                        results['documents'].append(result)
                        processed += 1

                        # Envia atualização de progresso
                        progress_update = {
                            'type': 'progress',
                            'total': total_files,
                            'processed': processed,
                            'current_file': result['filename']
                        }
                        yield json.dumps(progress_update) + '\n'

            # Gera arquivo de relatório se necessário
            if results['summary']['underage_list'] or results['summary']['error_files']:
                report_content = "RELATÓRIO DE VERIFICAÇÃO DE IDADE\n"
                report_content += "=" * 50 + "\n\n"

                if results['summary']['underage_list']:
                    report_content += "MENORES DE IDADE ENCONTRADOS:\n"
                    report_content += "-" * 30 + "\n"
                    for person in results['summary']['underage_list']:
                        report_content += f"Nome: {person['name']}\n"
                        report_content += f"Idade: {person['age']}\n"
                        report_content += f"Data de Nascimento: {person['date_of_birth']}\n"
                        report_content += f"Arquivo: {person['filename']}\n\n"

                if results['summary']['error_files']:
                    report_content += "\nARQUIVOS COM ERRO:\n"
                    report_content += "-" * 30 + "\n"
                    for file in results['summary']['error_files']:
                        report_content += f"Arquivo: {file['filename']}\n"
                        report_content += f"Erro: {file['error']}\n\n"

                # Salva o relatório
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_filename = f"relatorio_verificacao_{timestamp}.txt"
                with open(report_filename, "w", encoding="utf-8") as f:
                    f.write(report_content)

                results['report_filename'] = report_filename

            # Envia resultado final
            yield json.dumps(results)

        except Exception as e:
            error_response = {
                'status': 'error',
                'message': str(e)
            }
            yield json.dumps(error_response)

            # Gera arquivo de relatório se necessário
            if results['summary']['underage_list'] or results['summary']['error_files']:
                report_content = "RELATÓRIO DE VERIFICAÇÃO DE IDADE\n"
                report_content += "=" * 50 + "\n\n"

                if results['summary']['underage_list']:
                    report_content += "MENORES DE IDADE ENCONTRADOS:\n"
                    report_content += "-" * 30 + "\n"
                    for person in results['summary']['underage_list']:
                        report_content += f"Nome: {person['name']}\n"
                        report_content += f"Idade: {person['age']}\n"
                        report_content += f"Data de Nascimento: {person['date_of_birth']}\n"
                        report_content += f"Arquivo: {person['filename']}\n\n"

                if results['summary']['error_files']:
                    report_content += "\nARQUIVOS COM ERRO:\n"
                    report_content += "-" * 30 + "\n"
                    for file in results['summary']['error_files']:
                        report_content += f"Arquivo: {file['filename']}\n"
                        report_content += f"Erro: {file['error']}\n\n"

                # Salva o relatório
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_filename = f"relatorio_verificacao_{timestamp}.txt"
                with open(report_filename, "w", encoding="utf-8") as f:
                    f.write(report_content)

                results['report_filename'] = report_filename

            # Envia resultado final
            yield json.dumps(results)

        except Exception as e:
            error_response = {
                'status': 'error',
                'message': str(e)
            }
            yield json.dumps(error_response)


# Inicializa serviço OCR com verificação
aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
aws_region = os.getenv('AWS_REGION', 'us-east-2')
bucket = os.getenv('S3_BUCKET')

if not all([aws_access_key, aws_secret_key, bucket]):
    print("⚠️ Atenção: Credenciais AWS ou bucket não configurados!")
    print(f"AWS_ACCESS_KEY_ID: {'Configurado' if aws_access_key else 'Não configurado'}")
    print(f"AWS_SECRET_ACCESS_KEY: {'Configurado' if aws_secret_key else 'Não configurado'}")
    print(f"S3_BUCKET: {'Configurado' if bucket else 'Não configurado'}")

ocr_service = OCRService(
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name=aws_region
)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/process")
async def process_documents(file: UploadFile = File(...)):
    """
    Processa arquivo ZIP contendo documentos
    """
    try:
        if not file.filename.endswith('.zip'):
            raise HTTPException(status_code=400, detail="Apenas arquivos ZIP são aceitos")

        file_content = await file.read()
        return StreamingResponse(
            ocr_service.process_stream(file_content),
            media_type='application/x-ndjson'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)