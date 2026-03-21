from rest_framework.response import Response

def success_response(data=None, message="Success", status_code=200):
    return Response({
        "success": True,
        "data": data,
        "message": message
    }, status=status_code)

def error_response(message="Error", status_code=400, errors=None):
    response_data = {
        "success": False,
        "data": None,
        "message": message
    }
    if errors is not None:
        response_data["errors"] = errors
        
    return Response(response_data, status=status_code)
