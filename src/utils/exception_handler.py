from rest_framework.views import exception_handler

def custom_exception_handler(exc, context):
    # Standard DRF exception handler
    response = exception_handler(exc, context)

    if response is not None:
        response.data = {
            'success': False,
            'message': 'Error occurred',
            'errors': response.data
        }

    return response
