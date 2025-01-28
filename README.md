# AWS Textract OCR API Service

A FastAPI-based service for extracting and searching text in documents using AWS Textract. This service allows you to upload documents (PDF, PNG, JPEG) and search for specific names within the extracted text.

## 🚀 Features

- Document text extraction using AWS Textract
- Name search functionality within extracted text
- Automatic S3 file management
- FastAPI-based REST API
- Docker containerization
- Detailed API documentation
- Postman collection for testing

## 📋 Prerequisites

- Docker and Docker Compose
- AWS Account with Textract access
- AWS IAM credentials
- Python 3.9+ (for local development)

## 🛠️ Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/aws-ocr-service.git
cd aws-ocr-service
```

2. Create and configure the `.env` file:
```bash
cp .env.example .env
# Edit .env with your AWS credentials and settings
```

3. Build and start the service:
```bash
docker-compose up --build
```

## 🔧 Configuration

### Environment Variables

```env
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
S3_BUCKET=your-bucket-name
```

### AWS Setup

1. Create an S3 bucket
2. Create an IAM user with the following permissions:
   - AWSTextractFullAccess
   - S3 bucket access (custom policy)

## 📖 API Documentation

### Endpoints

#### POST /process
Process a document and search for names.

**Request:**
- Method: POST
- Content-Type: multipart/form-data
- Parameters:
  - `file`: Document file (PDF, PNG, JPEG)
  - `search_request`: JSON string containing names to search

**Example Request:**
```python
import requests

url = "http://localhost:8000/process"
files = {
    'file': ('document.pdf', open('document.pdf', 'rb')),
    'search_request': (None, '{"names": ["John Doe", "Jane Smith"]}')
}

response = requests.post(url, files=files)
print(response.json())
```

**Example Response:**
```json
{
    "status": "success",
    "text": "extracted text content...",
    "found_names": {
        "john doe": [1, 5],
        "jane smith": [3]
    }
}
```

## 🧪 Testing

### Using Postman

1. Import the provided Postman collection
2. Configure environment variables
3. Use the pre-configured requests

### Using curl

```bash
curl -X POST "http://localhost:8000/process" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@document.pdf" \
  -F 'search_request={"names": ["John Doe", "Jane Smith"]}'
```

## 📁 Project Structure

```
aws-ocr-service/
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── docker-compose.yml
├── main.py
├── requirements.txt
└── postman_collection.json
```

## 🔐 Security Considerations

- Never commit `.env` file
- Use IAM roles in production
- Regularly rotate AWS credentials
- Implement rate limiting in production
- Add authentication for API access

## 🚀 Deployment

### Local Development
```bash
docker-compose up
```

### Production
1. Update environment variables
2. Build the Docker image
3. Deploy using your preferred container orchestration

## 📈 Monitoring and Logging

- AWS CloudWatch integration (planned)
- Error tracking
- Performance monitoring
- Request logging

## 🛣️ Roadmap

- [ ] Add authentication
- [ ] Implement rate limiting
- [ ] Add unit tests
- [ ] Set up CI/CD pipeline
- [ ] Add monitoring and logging
- [ ] Support more document types
- [ ] Add batch processing

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📧 Contact

Your Name - your.email@example.com
Project Link: https://github.com/yourusername/aws-ocr-service

## 🙏 Acknowledgments

- FastAPI documentation
- AWS Textract documentation
- Docker documenta
