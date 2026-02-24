import json
import traceback
from functools import wraps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse


def handle_pos_errors(func):
    """Decorator to centralize error handling for POS endpoints.

    - Catches ValidationError and Exception
    - If request is HTMX, returns an HttpResponse with HX-Trigger posToast
      so the frontend can show a toast. Otherwise returns plain HttpResponse.
    """
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        try:
            result = func(request, *args, **kwargs)

            # Si la vista devolvió HttpResponse con error, convertirlo a posToast en HTMX
            if isinstance(result, HttpResponse) and getattr(result, 'status_code', 200) >= 400:
                is_hx = request.headers.get("HX-Request") == "true" or request.META.get("HTTP_HX_REQUEST") == "true"
                if is_hx:
                    try:
                        body = result.content.decode('utf-8', errors='ignore')
                        # acortar un poco
                        body = (body or '').strip()
                    except Exception:
                        body = str(result)
                    result["HX-Trigger"] = json.dumps({"posToast": {"message": body}})
                return result

            return result

        except ValidationError as e:
            # extract messages
            try:
                msgs = e.messages
                if msgs:
                    msg = ", ".join(str(m) for m in msgs)
                else:
                    msg = str(e)
            except Exception:
                msg = str(e)

            if request.headers.get("HX-Request") == "true" or request.META.get("HTTP_HX_REQUEST") == "true":
                resp = HttpResponse(msg, status=400)
                resp["HX-Trigger"] = json.dumps({"posToast": {"message": msg}})
                return resp
            return HttpResponse(msg, status=400)
        except Exception as e:
            # log server-side
            traceback.print_exc()
            msg = str(e) if settings.DEBUG else "Error interno del servidor"
            if request.headers.get("HX-Request") == "true" or request.META.get("HTTP_HX_REQUEST") == "true":
                resp = HttpResponse(msg, status=500)
                resp["HX-Trigger"] = json.dumps({"posToast": {"message": msg}})
                return resp
            return HttpResponse(msg, status=500)
    return wrapper
