from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiResponse

from .serializers import (
    UserRegistrationSerializer,
    UserLoginSerializer,
    UserAccountSerializer,
    LogoutSerializer,
)
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from utils.cache import CacheService
from utils.cache_key import user_profile_key

class RegisterView(APIView):
    permission_classes = [AllowAny]
    
    @extend_schema(
        request=UserRegistrationSerializer,
        responses={
            201: OpenApiResponse(
                description='Registration successful',
                examples=[
                    OpenApiExample('Success Response', value={'success': True, 'message': 'Registration successful'})
                ]
            ),
            400: OpenApiResponse(description='Bad Request - Validation errors')
        },
        tags=['Authentication'],
        summary='Register a new user',
        description='Create a new user account.'
    )
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        
        if serializer.is_valid():
            user = serializer.save()
            return Response({
                'success': True,
                'message': 'Registration successful'
            }, status=status.HTTP_201_CREATED)
        
        return Response({
            'success': False,
            'message': 'Registration failed',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

class LoginView(APIView):
    permission_classes = [AllowAny]
    
    @extend_schema(
        request=UserLoginSerializer,
        responses={
            200: OpenApiResponse(
                description='Login successful',
                examples=[
                    OpenApiExample(
                        'Success Response',
                        value={
                            'success': True,
                            'message': 'Login successful',
                            'data': {
                                'access': 'eyJ0e...g',
                                'refresh': 'eyJ0e...g'
                            }
                        }
                    )
                ]
            ),
            401: OpenApiResponse(description='Unauthorized - Invalid credentials')
        },
        tags=['Authentication'],
        summary='Login user',
        description='Authenticate using username or email along with password.'
    )
    def post(self, request):
        serializer = UserLoginSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            tokens = serializer.validated_data['tokens']
            return Response({
                'success': True,
                'message': 'Login successful',
                'data': tokens
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'message': 'Login failed',
            'errors': serializer.errors
        }, status=status.HTTP_401_UNAUTHORIZED)

class TokenRefreshView(APIView):
    permission_classes = [AllowAny]
    
    @extend_schema(
        request=TokenRefreshSerializer,
        responses={
            200: OpenApiResponse(
                description='Token refreshed successfully',
                examples=[
                    OpenApiExample(
                        'Success Response',
                        value={
                            'success': True,
                            'message': 'Token refreshed successfully',
                            'data': {
                                'access': 'eyJ0eXAi...g',
                                'refresh': 'eyJ0eXAi...g'
                            }
                        }
                    )
                ]
            ),
            401: OpenApiResponse(description='Unauthorized - Invalid or expired refresh token')
        },
        tags=['Authentication'],
        summary='Refresh access token',
        description='Use a valid refresh token to obtain a new access token.'
    )
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        
        if serializer.is_valid():
            return Response({
                'success': True,
                'message': 'Token refreshed successfully',
                'data': {
                    'access': serializer.validated_data['access'],
                    'refresh': serializer.validated_data.get('refresh', request.data.get('refresh')),
                }
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'message': 'Token refresh failed',
            'errors': serializer.errors
        }, status=status.HTTP_401_UNAUTHORIZED)

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        request=LogoutSerializer,
        responses={
            200: OpenApiResponse(description='Logout successful'),
            400: OpenApiResponse(description='Bad Request - Invalid token')
        },
        tags=['Authentication'],
        summary='Logout user',
        description='Blacklist the refresh token to logout the user.'
    )
    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            
            return Response({
                'success': True,
                'message': 'Logout successful'
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({
                'success': False,
                'message': 'Logout failed',
                'errors': {'detail': str(e)}
            }, status=status.HTTP_400_BAD_REQUEST)

class MeView(APIView):
    """
    Current User Profile Endpoint
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=UserAccountSerializer,
                description='User profile retrieved successfully'
            ),
            401: OpenApiResponse(description='Unauthorized')
        },
        tags=['User Profile'],
        summary='Get current user profile',
        description='Retrieve authenticated user profile.'
    )
    def get(self, request):
        cache_key = user_profile_key(request.user.id)
        cached_user = CacheService.get(cache_key)

        if cached_user:
            return Response({
                'success': True,
                'data': {
                    'user': cached_user
                }
            }, status=status.HTTP_200_OK)

        user_data = UserAccountSerializer(request.user).data
        CacheService.set(cache_key, user_data, timeout=300)

        return Response({
            'success': True,
            'data': {
                'user': user_data
            }
        }, status=status.HTTP_200_OK)

class UpdateProfileView(APIView):
    """
    Update User Profile Endpoint
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=UserAccountSerializer,
        responses={
            200: OpenApiResponse(
                response=UserAccountSerializer,
                description='User profile updated successfully'
            ),
            400: OpenApiResponse(description='Bad Request - Validation errors'),
            401: OpenApiResponse(description='Unauthorized')
        },
        tags=['User Profile'],
        summary='Update current user profile',
        description='Update authenticated user profile information.'
    )
    def patch(self, request):
        serializer = UserAccountSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={'request': request}
        )
        
        if serializer.is_valid():
            user = serializer.save()
            cache_key = user_profile_key(request.user.id)
            CacheService.delete(cache_key)
            
            return Response({
                'success': True,
                'message': 'Profile updated successfully',
                'data': {
                    'user': UserAccountSerializer(user).data
                }
            }, status=status.HTTP_200_OK)
        
        return Response({
            'success': False,
            'message': 'Profile update failed',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
