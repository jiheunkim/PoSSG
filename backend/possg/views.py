import os
from django.http import FileResponse
import shutil
import jwt
import json
from datetime import datetime
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from django.utils.text import slugify
from django.core.files.storage import default_storage
from .models import File, Portfolio
from .serializers import *
import boto3
from botocore.exceptions import NoCredentialsError
from pdf2image import convert_from_path
from common.models import User
from .utils import get_user_folders_info
import PyPDF2  # PdfMerger 모듈 임포트
import sys
sys.path.append('/home/honglee0317/possg/backend/possg')
sys.path.append('/home/honglee0317/possg/backend/config')
import tp
from config.my_settings import *


from pathlib import Path

class CreateUserFolder(APIView):
    def post(self, request):
        token = request.headers.get('Authorization', None)
        if token is None:
            raise AuthenticationFailed('Authorization token not provided')

        if not token.startswith('Bearer '):
            raise AuthenticationFailed('Invalid token format')
        token = token.split('Bearer ')[1]

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        
        sector = request.data.get('sector')
        title = request.data.get('title')
        new_title = request.data.get('new_title')
        is_exist = request.data.get('is_Exist')
        user_name = user.nickname
        
        base_path = settings.MEDIA_ROOT
        current_path = os.path.join(base_path, user_name, sector, title)
        new_path = os.path.join(base_path, user_name, sector, new_title)

        if is_exist == 0:
            if not os.path.exists(current_path):
                os.makedirs(current_path)
                return JsonResponse({"message": "Folder created"})
            else:
                return JsonResponse({"message": "Folder already exists"}, status=400)
        elif is_exist == 1:
            if os.path.exists(current_path):
                if os.path.exists(new_path):
                    return JsonResponse({'message': 'same'})
                else:
                    os.rename(current_path, new_path)
                    return JsonResponse({'success': f'Folder renamed from {title} to {new_title}'})
            else:
                return JsonResponse({'error': 'Original folder does not exist'}, status=404)
        elif is_exist == 2:
            if os.path.exists(current_path):
                shutil.rmtree(current_path)
                return JsonResponse({"message": "Folder deleted"})
            else:
                return JsonResponse({"message": "Folder does not exist"}, status=404)
        else:
            return JsonResponse({"message": "Invalid is_Exist value"}, status=400)

def upload_to_aws(file_obj, bucket, s3_file_name):
    session = boto3.Session(
        aws_access_key_id=aws_id,
        aws_secret_access_key=aws_key,
        region_name='us-east-2'
    )
    s3 = session.client('s3')

    try:
        s3.upload_fileobj(file_obj, bucket, s3_file_name)
        url = f"https://{bucket}.s3.{s3.meta.region_name}.amazonaws.com/{s3_file_name}"
        return url
    except NoCredentialsError:
        print("Credentials not available")
        return None

class ImageUploadView(APIView):
    def post(self, request, *args, **kwargs):
        token = request.headers.get('Authorization').split()[1]
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        user_name = user.nickname
        
        sector = request.data.get('sector')
        folder_name = request.data.get('folderName')
        file = request.FILES['file']

        if not sector or not folder_name or not file:
            return Response({"error": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)
        
        new_filename = "thumbnail.jpg"
        s3_file_path = os.path.join("user_uploads", user_name, sector, folder_name, "thumbnails", new_filename)

        s3_url = upload_to_aws(file, 'possg', s3_file_path)

        print("s3_url:", s3_url)
        if not s3_url:
            return Response({"error": "File upload failed."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        image_instance = File(
            user=user,
            sector=sector,
            title=folder_name,
            file_type='image',
            file_name=new_filename,
            thumbnail=s3_url
        )
        image_instance.save()
        
        return Response({"message": "Upload success", "url": s3_url}, status=status.HTTP_201_CREATED)


class UserFoldersInfoView(APIView):
    def get(self, request, *args, **kwargs):
        token = request.headers.get('Authorization').split()[1]
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        user_name = user.nickname
        
        bucket_name = 'possg'
        folders_info = get_user_folders_info(bucket_name, user_name)
        
        print("folders:", folders_info)
        
        return Response({"folders": folders_info}, status=status.HTTP_200_OK)


class FileUploadView(APIView):
    def post(self, request, *args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            raise AuthenticationFailed('Authorization header missing or invalid')

        token = auth_header.split()[1]

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        user_name = slugify(user.nickname)

        serializer = MultiFileUploadSerializer(data=request.data)
        if serializer.is_valid():
            sector = serializer.validated_data['sector']
            title = serializer.validated_data['title']
            files = serializer.validated_data['files']
            response_files = []

            for file in files:
                print("file:", file)
                upload_serializer = FileUploadSerializer(data={
                    'sector': sector,
                    'title': title,
                    'file': file
                })
                if upload_serializer.is_valid():
                    instance = upload_serializer.save()
                    new_path = os.path.join(settings.MEDIA_ROOT, user_name, sector, title, "pdf_thumbnails", os.path.basename(instance.file.name))
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    os.rename(instance.file.path, new_path)
                    instance.file.name = os.path.join(user_name, sector, title, os.path.basename(instance.file.name))
                    instance.save()

                    file_type = 'image' if instance.file.name.lower().endswith(('.png', '.jpg', '.jpeg')) else 'pdf'
                    thumbnail = None
                    if file_type == 'pdf':
                        images = convert_from_path(new_path, first_page=1, last_page=1)
                        if images:
                            pdf_image_path = f"{new_path}.png"
                            print("pimage:",pdf_image_path)
                            images[0].save(pdf_image_path, 'PNG')
                            thumbnail_path = os.path.join("user_uploads", 'pdf_thumbnails', user_name, sector, title, pdf_image_path.split('/')[-1])
                            print("thumb:", thumbnail_path)
                            file_obj = open(pdf_image_path, 'rb')
                            thumbnail = upload_to_aws(file_obj, 'possg', thumbnail_path)
                            print("aws_thumb:", thumbnail)
                    elif file_type == 'image':
                        thumbnail_path = os.path.join("user_uploads", user_name, sector, title, file.name)
                        thumbnail = upload_to_aws(file, 'possg', thumbnail_path)
                        print("aws_thumb:", thumbnail)
                        
                        
                    File.objects.create(
                        user=user,
                        sector=sector,
                        title=title,
                        file_type=file_type,
                        file_name=os.path.basename(instance.file.name),
                        thumbnail=thumbnail
                    )

                    response_files.append({
                        "file": thumbnail,
                        "src": thumbnail
                    })
                else:
                    return Response(upload_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                "sector": sector,
                "title": title,
                "files": response_files
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    
class SearchFilesView(APIView):
    def post(self, request, *args, **kwargs):
        token = request.headers.get('Authorization', None)
        if not token or not token.startswith('Bearer '):
            raise AuthenticationFailed('Authorization token not provided or invalid format')

        token = token.split('Bearer ')[1]
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        user_name = user.nickname

        sector = request.data.get('sector')
        title = request.data.get('title')

        if not sector or not title:
            return Response({"error": "Sector and title are required"}, status=status.HTTP_400_BAD_REQUEST)

        base_path = os.path.join(settings.MEDIA_ROOT, user_name, sector, title)
        response_files = []

        if os.path.exists(base_path):
            for root, dirs, files in os.walk(base_path):
                for file in files:
                    print(file)
                    file_obj = get_object_or_404(File, user = user, sector = sector, title = title, file_name = file)
                    relative_path = os.path.relpath(os.path.join(root, file), settings.MEDIA_ROOT)
                    file_url = os.path.join(settings.MEDIA_URL, relative_path).replace('\\', '/')
                    src_url = file_obj.thumbnail

                    print("file_url:", file_url, "src_url:", src_url)

                    if file.lower().endswith('.pdf'):
                        try:
                            temp_path = os.path.join(root, file)
                            if temp_path.endswith('.PDF'):
                                new_path = temp_path.replace('.PDF', '.pdf')
                                os.rename(temp_path, new_path)
                            else:
                                new_path = temp_path

                            images = convert_from_path(new_path, first_page=1, last_page=1)
                            if images:
                                pdf_image_path = os.path.join(settings.MEDIA_ROOT, 'pdf_thumbnails')
                                os.makedirs(pdf_image_path, exist_ok=True)
                                image_path = os.path.join(pdf_image_path, file).replace('.pdf', '.png')
                                images[0].save(image_path, 'PNG')
                                
                                image_relative_path = os.path.relpath(image_path, settings.MEDIA_ROOT)
                                
                                src_url = os.path.join(settings.MEDIA_URL, image_relative_path).replace('\\', '/')
                        except Exception as e:
                            print(f"Error converting PDF to image: {e}")
                    elif file.lower().endswith('.png') or file.lower().endswith('.jpg'):
                        file_url = os.path.join(settings.MEDIA_URL, relative_path).replace('\\', '/')
                        src_url = file_url
                        
                        print(file_url)
                            
                    response_files.append({
                        "file": file,
                        "src": src_url
                    })

            portfolio_instance = Portfolio.objects.filter(user=user, sector=sector, title=title).first()
            if portfolio_instance:
                portfolio_summary = portfolio_instance.summary
                return Response({
                    "sector": sector,
                    "title": title,
                    "files": response_files,
                    "folder_portfolio": portfolio_summary
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "sector": sector,
                    "title": title,
                    "files": response_files,
                    "folder_portfolio": ''
                }, status=status.HTTP_200_OK)
        else:
            return Response({
                "sector": sector,
                "title": title,
                "files": [],
                "folder_portfolio": ''
            }, status=status.HTTP_200_OK)
            
    

class DeleteUserFileView(APIView):
    def post(self, request):
        token = request.headers.get('Authorization', None)
        if token is None:
            raise AuthenticationFailed('Authorization token not provided')

        if not token.startswith('Bearer '):
            raise AuthenticationFailed('Invalid token format')
        token = token.split('Bearer ')[1]

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid') 

        user = get_object_or_404(User, pk=user_id)
        
        sector = request.data.get('sector')
        title = request.data.get('title')
        file_name = request.data.get('file_name')
        user_name = user.nickname
        
        base_path = settings.MEDIA_ROOT
        file_path = os.path.join(base_path, user_name, sector, title, file_name)

        if os.path.exists(file_path):
            os.remove(file_path)
            File.objects.filter(user=user, sector=sector, title=title, file_name=file_name).delete()
            return JsonResponse({"message": "File deleted"})
        else:
            return JsonResponse({"message": "File does not exist"}, status=404)


class PortfolioByFolderView(APIView):
    def post(self, request, *args, **kwargs):
        token = request.headers.get('Authorization', None)
        if token is None:
            raise AuthenticationFailed('Authorization token not provided')

        if not token.startswith('Bearer '):
            raise AuthenticationFailed('Invalid token format')
        token = token.split('Bearer ')[1]

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        print("User:", user)
        user_name = user.nickname

        sector = request.data.get('sector')
        title = request.data.get('title')

        if not sector or not title:
            return Response({"error": "Sector and title are required"}, status=status.HTTP_400_BAD_REQUEST)

        
        folder_paths = os.path.join("/home/honglee0317/possg/backend/media/folders", user_name, sector, title)
        
        print("folder_paths:", folder_paths)
        
        
        print("tp:")
        summary, result = tp.summary(folder_paths, user_name, sector, title)
        
        print("summary:", summary, "result:", result)
        
        portfolio_folder = os.path.join(settings.MEDIA_ROOT, 'portfolios')
        os.makedirs(portfolio_folder, exist_ok=True)
        #portfolio_filename = f"{user_name}_{sector}_{title}.pdf"
        portfolio_filepath = f"""/home/honglee0317/possg/backend/media/folders/portfolio/{user_name}_{sector}_{title}.pdf"""

        # Assuming the PDF is created and saved at portfolio_filepath
        if not os.path.exists(portfolio_filepath):
            return Response({"error": "Portfolio file not found"}, status=status.HTTP_404_NOT_FOUND)

        # 저장 또는 업데이트 로직
        portfolio, created = Portfolio.objects.update_or_create(
            user=user,
            sector=sector,
            title=title,
            defaults={
                'summary': summary,
                'pdf_file': portfolio_filepath,
                'result': result
            }
        )
        print("portfolio created:", portfolio)
        response_data = { 
            "summary": portfolio.summary
        }

        return Response(response_data, status=status.HTTP_200_OK)

# PDF 파일들을 병합하는 함수
def merge_pdfs_from_folder(folder_path, output_path):
    # PdfMerger 객체 생성
    merger = PyPDF2.PdfMerger()
    
    # 폴더 내의 모든 파일을 확인
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isfile(item_path) and item.lower().endswith('.pdf'):
            merger.append(item_path)
    
    # 병합된 PDF를 출력 경로에 저장
    merger.write(output_path)
    merger.close()


class PortfolioMakeView(APIView):
    def get(self, request, *args, **kwargs):
        token = request.headers.get('Authorization', None)
        if token is None:
            raise AuthenticationFailed('Authorization token not provided')

        if not token.startswith('Bearer '):
            raise AuthenticationFailed('Invalid token format')
        token = token.split('Bearer ')[1]

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        user = get_object_or_404(User, pk=user_id)
        user_name = user.nickname

        portfolio_folder = os.path.join(settings.MEDIA_ROOT, user_name, 'portfolio')
        portfolio_filename = f"{user_name}_total.pdf"
        portfolio_filepath = os.path.join(portfolio_folder, portfolio_filename)
        print("portfolio_filepath:", portfolio_filepath)

        
        portfolios = Portfolio.objects.filter(user=user)

        results = [portfolio.result for portfolio in portfolios if portfolio.result]


        # 포트폴리오 폴더가 존재하지 않는 경우 폴더 생성
        if not os.path.exists(portfolio_folder):
            os.makedirs(portfolio_folder)

        # 포트폴리오 파일 병합
        merge_pdfs_from_folder(portfolio_folder, portfolio_filepath)

        sector = portfolios[0].sector
        title = portfolios[0].title

        if os.path.exists(portfolio_filepath):
            print("File exists")
            #return FileResponse(open(portfolio_filepath, 'rb'), content_type='application/pdf')
            
            response_data = []
            for i in range(len(results)):
                response_data.append({"sector": sector,
                    "folderName": title,
                    "results": results[i] })
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Portfolio file not found"
            }, status=status.HTTP_404_NOT_FOUND)
            

class UserPortfolioView(APIView):
    def get(self, request, *args, **kwargs):
        # Authorization 헤더에서 JWT 토큰 추출
        token = request.headers.get('Authorization', None)
        if token is None:
            raise AuthenticationFailed('Authorization token not provided')

        if not token.startswith('Bearer '):
            raise AuthenticationFailed('Invalid token format')
        token = token.split('Bearer ')[1]

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        # 사용자 객체 가져오기
        user = get_object_or_404(User, pk=user_id)
        user_name = user.nickname
        portfolios = Portfolio.objects.filter(user=user)
        results = [portfolio.result for portfolio in portfolios if portfolio.result]
        '''
        # 파일 경로 설정
        sector = request.data.get('sector')
        title = request.data.get('title')
        if not sector or not title:
            print("Missing 'sector' or 'title' query parameter")
            return Response({
                "error": "Missing 'sector' or 'title' query parameter"
            }, status=status.HTTP_400_BAD_REQUEST)


        portfolio_folder = os.path.join(settings.MEDIA_ROOT, 'folders', 'portfolio')
        portfolio_filename = f"{user_name}_{sector}_{title}.pdf"
        
        portfolio_filepath = os.path.join(portfolio_folder, portfolio_filename)
        '''
        
        sector = portfolios[0].sector
        title = portfolios[0].title


        response_data = []
        for i in range(len(results)):
            response_data.append({"sector": sector,
                "folderName": title,
                "results": results[i] })
        return Response(response_data, status=status.HTTP_200_OK)

        if os.path.exists(portfolio_filepath):
            print("File exists")
            #return FileResponse(open(portfolio_filepath, 'rb'), content_type='application/pdf')
            
            response_data = []
            for i in range(len(results)):
                response_data.append({"sector": sector,
                    "folderName": title,
                    "results": results[i] })
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Portfolio file not found"
            }, status=status.HTTP_404_NOT_FOUND)
            
            
class RecommendView(APIView):
    def get(self, request, *args, **kwargs):
        # Authorization 헤더에서 JWT 토큰 추출
        token = request.headers.get('Authorization', None)
        if token is None:
            raise AuthenticationFailed('Authorization token not provided')

        if not token.startswith('Bearer '):
            raise AuthenticationFailed('Invalid token format')
        token = token.split('Bearer ')[1]

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Token payload invalid')

        # 사용자 객체 가져오기
        user = get_object_or_404(User, pk=user_id)
        user_name = user.nickname
        portfolios = Portfolio.objects.filter(user=user)
        results = [portfolio.result for portfolio in portfolios if portfolio.result]
        msg = tp.Recommend(results)
        print("msg:", msg)
        
        
        
        return Response({"message":msg}, status=status.HTTP_200_OK)