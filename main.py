from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
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
import uuid

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        self.bucket = os.getenv('S3_BUCKET')

    def normalize_filename(self, filename: str) -> str:
        """Normaliza o nome do arquivo para evitar espaços e caracteres especiais."""
        normalized = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        return normalized

    def upload_zip_to_s3(self, file_content: bytes, process_id: str) -> str:
        """Upload do ZIP para uma pasta temporária no S3."""
        zip_key = f"temp/{process_id}/upload.zip"
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=zip_key,
                Body=file_content
            )
            return zip_key
        except Exception as e:
            print(f"Erro no upload do ZIP: {str(e)}")
            raise

    async def extract_zip_in_s3(self, zip_key: str, process_id: str) -> List[str]:
        """Extrai o ZIP diretamente no S3."""
        try:
            # Lê o ZIP do S3
            response = self.s3.get_object(Bucket=self.bucket, Key=zip_key)
            zip_content = response['Body'].read()

            extracted_files = []
            with zipfile.ZipFile(BytesIO(zip_content)) as zip_ref:
                for file_info in zip_ref.filelist:
                    if file_info.filename.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg')):
                        # Normaliza o nome do arquivo
                        normalized_filename = self.normalize_filename(file_info.filename)

                        # Lê o arquivo do ZIP
                        file_content = zip_ref.read(file_info.filename)

                        # Define o novo caminho no S3
                        new_key = f"temp/{process_id}/extracted/{normalized_filename}"

                        # Upload do arquivo extraído
                        self.s3.put_object(
                            Bucket=self.bucket,
                            Key=new_key,
                            Body=file_content
                        )
                        extracted_files.append(new_key)

            # Remove o arquivo ZIP original
            self.s3.delete_object(Bucket=self.bucket, Key=zip_key)
            return extracted_files

        except Exception as e:
            print(f"Erro na extração do ZIP: {str(e)}")
            raise

    async def process_documents_in_parallel(self, file_keys: List[str]) -> List[Dict]:
        """Processa múltiplos documentos em paralelo."""

        async def process_single_document(file_key: str):
            try:
                # Verifica se o objeto existe no S3
                self.s3.head_object(Bucket=self.bucket, Key=file_key)

                # Inicia job do Textract
                response = self.textract.start_document_text_detection(
                    DocumentLocation={
                        'S3Object': {
                            'Bucket': self.bucket,
                            'Name': file_key
                        }
                    }
                )
                job_id = response['JobId']

                # Aguarda conclusão
                while True:
                    response = self.textract.get_document_text_detection(JobId=job_id)
                    if response['JobStatus'] in ['SUCCEEDED', 'FAILED']:
                        break
                    await asyncio.sleep(1)

                if response['JobStatus'] == 'FAILED':
                    return {'filename': file_key, 'error': 'Falha na extração do texto'}

                # Extrai texto
                text = ""
                for item in response['Blocks']:
                    if item['BlockType'] == 'LINE':
                        text += item['Text'] + "\n"

                # Processa data de nascimento
                date_of_birth = self.extract_date_of_birth(text)
                if date_of_birth:
                    age_info = self.calculate_age(date_of_birth)
                else:
                    age_info = {'error': 'Data de nascimento não encontrada'}

                return {
                    'filename': os.path.basename(file_key),
                    'text': text,
                    'age_verification': age_info
                }

            except Exception as e:
                return {'filename': file_key, 'error': str(e)}
            finally:
                # Remove arquivo do S3
                try:
                    self.s3.delete_object(Bucket=self.bucket, Key=file_key)
                except:
                    pass

        # Processa todos os documentos em paralelo
        tasks = [process_single_document(key) for key in file_keys]
        return await asyncio.gather(*tasks)

    def extract_date_of_birth(self, text: str) -> str:
        pattern = r'\d{2}/\d{2}/\d{4}'
        dates = re.findall(pattern, text)

        if len(dates) >= 2:
            date_objects = []
            for date_str in dates:
                try:
                    date_obj = datetime.strptime(date_str, '%d/%m/%Y')
                    date_objects.append((date_str, date_obj))
                except ValueError:
                    continue

            if date_objects:
                date_objects.sort(key=lambda x: x[1])
                return date_objects[0][0]
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
            return {'error': f"Erro ao calcular idade: {str(e)}"}

    async def process_stream(self, file_content: bytes) -> AsyncGenerator[str, None]:
        try:
            process_id = str(uuid.uuid4())
            print(f"Iniciando processamento {process_id}")

            # Upload do ZIP para S3
            yield json.dumps({
                'type': 'progress',
                'stage': 'upload',
                'processed': 0,
                'total': 3,  # Total de estágios: upload, extração, processamento
                'message': 'Enviando ZIP para processamento...'
            }) + '\n'

            zip_key = self.upload_zip_to_s3(file_content, process_id)
            print(f"ZIP enviado para S3: {zip_key}")

            # Extração dos arquivos
            yield json.dumps({
                'type': 'progress',
                'stage': 'extraction',
                'processed': 1,
                'total': 3,
                'message': 'Extraindo arquivos do ZIP...'
            }) + '\n'

            extracted_files = await self.extract_zip_in_s3(zip_key, process_id)
            total_files = len(extracted_files)
            print(f"Arquivos extraídos: {total_files}")

            yield json.dumps({
                'type': 'progress',
                'stage': 'processing',
                'processed': 2,
                'total': 3,
                'message': f'Processando {total_files} documentos...'
            }) + '\n'

            # Processa os documentos e acompanha o progresso
            results = []
            for index, file_key in enumerate(extracted_files, 1):
                try:
                    print(f"Processando arquivo {index}/{total_files}: {file_key}")

                    # Inicia job do Textract
                    response = self.textract.start_document_text_detection(
                        DocumentLocation={
                            'S3Object': {
                                'Bucket': self.bucket,
                                'Name': file_key
                            }
                        }
                    )
                    job_id = response['JobId']
                    print(f"Job Textract iniciado: {job_id}")

                    # Aguarda processamento do Textract
                    while True:
                        response = self.textract.get_document_text_detection(JobId=job_id)
                        status = response['JobStatus']
                        print(f"Status do job {job_id}: {status}")

                        if status in ['SUCCEEDED', 'FAILED']:
                            break
                        await asyncio.sleep(2)

                    if status == 'FAILED':
                        raise Exception(f"Falha no processamento do Textract: {job_id}")

                    # Extrai texto
                    text = ""
                    for item in response['Blocks']:
                        if item['BlockType'] == 'LINE':
                            text += item['Text'] + "\n"

                    # Processa data de nascimento
                    date_of_birth = self.extract_date_of_birth(text)
                    if date_of_birth:
                        age_info = self.calculate_age(date_of_birth)
                    else:
                        age_info = {'error': 'Data de nascimento não encontrada'}

                    result = {
                        'filename': os.path.basename(file_key),
                        'text': text,
                        'age_verification': age_info
                    }
                    results.append(result)

                    # Envia progresso do processamento
                    yield json.dumps({
                        'type': 'progress',
                        'processed': index,
                        'total': total_files,
                        'current_file': os.path.basename(file_key),
                        'message': f'Processando documento {index} de {total_files}'
                    }) + '\n'

                except Exception as e:
                    print(f"Erro no processamento do arquivo {file_key}: {str(e)}")
                    results.append({
                        'filename': os.path.basename(file_key),
                        'error': str(e)
                    })
                finally:
                    # Remove arquivo processado do S3
                    try:
                        self.s3.delete_object(Bucket=self.bucket, Key=file_key)
                        print(f"Arquivo removido do S3: {file_key}")
                    except Exception as e:
                        print(f"Erro ao remover arquivo {file_key}: {str(e)}")

            # Prepara resultado final
            final_results = {
                'documents': [],
                'summary': {
                    'total_processed': len(results),
                    'underage_count': 0,
                    'adult_count': 0,
                    'error_count': 0,
                    'underage_list': [],
                    'error_files': []
                }
            }

            # Processa resultados
            for result in results:
                if 'error' in result:
                    final_results['summary']['error_count'] += 1
                    final_results['summary']['error_files'].append(result)
                elif result.get('age_verification'):
                    age_info = result['age_verification']
                    if age_info.get('is_underage'):
                        final_results['summary']['underage_count'] += 1
                        name = os.path.splitext(os.path.basename(result['filename']))[0]
                        final_results['summary']['underage_list'].append({
                            'name': name,
                            'age': age_info['age'],
                            'date_of_birth': age_info['date_of_birth'],
                            'filename': result['filename']
                        })
                    else:
                        final_results['summary']['adult_count'] += 1
                final_results['documents'].append(result)

            # Gera relatório
            if final_results['summary']['underage_list'] or final_results['summary']['error_files']:
                report_content = self.generate_report(final_results)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_filename = f"relatorio_verificacao_{timestamp}.txt"
                with open(report_filename, "w", encoding="utf-8") as f:
                    f.write(report_content)
                final_results['report_filename'] = report_filename

            print(f"Processamento {process_id} concluído")
            yield json.dumps(final_results)

        except Exception as e:
            print(f"Erro no processamento: {str(e)}")
            yield json.dumps({
                'status': 'error',
                'message': str(e)
            })

    def generate_report(self, results: Dict) -> str:
        report = "RELATÓRIO DE VERIFICAÇÃO DE IDADE\n"
        report += "=" * 50 + "\n\n"

        if results['summary']['underage_list']:
            report += "MENORES DE IDADE ENCONTRADOS:\n"
            report += "-" * 30 + "\n"
            for person in results['summary']['underage_list']:
                report += f"Nome: {person['name']}\n"
                report += f"Idade: {person['age']}\n"
                report += f"Data de Nascimento: {person['date_of_birth']}\n"
                report += f"Arquivo: {person['filename']}\n\n"

        if results['summary']['error_files']:
            report += "\nARQUIVOS COM ERRO:\n"
            report += "-" * 30 + "\n"
            for file in results['summary']['error_files']:
                report += f"Arquivo: {file['filename']}\n"
                report += f"Erro: {file['error']}\n\n"

        return report


# Inicializa serviço OCR
aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
aws_region = os.getenv('AWS_REGION', 'us-east-2')

if not all([aws_access_key, aws_secret_key]):
    print("⚠️ Atenção: Credenciais AWS não configuradas!")

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
    """Processa arquivo ZIP contendo documentos"""
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


@app.get("/download/{filename}")
async def download_file(filename: str):
    """Rota para download do relatório"""
    file_path = f"./{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename)
    else:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)