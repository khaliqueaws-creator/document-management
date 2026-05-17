from functools import wraps
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.conf import settings


def get_okta_groups(request):
    user = request.session.get("user", {})
    return user.get("groups", [])


def is_viewer(request):
    groups = get_okta_groups(request)
    return (
        settings.OKTA_GROUP_VIEWER in groups
        or settings.OKTA_GROUP_LOADER in groups
        or settings.OKTA_GROUP_ADMIN in groups
    )


def is_loader(request):
    groups = get_okta_groups(request)
    return (
        settings.OKTA_GROUP_LOADER in groups
        or settings.OKTA_GROUP_ADMIN in groups
    )


def is_admin(request):
    groups = get_okta_groups(request)
    return settings.OKTA_GROUP_ADMIN in groups


def okta_role_required(check_func):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            print("DEBUG SESSION USER:", request.session.get("user"), flush=True)
            print("DEBUG USER GROUPS:", get_okta_groups(request), flush=True)
            print("DEBUG OKTA_GROUP_VIEWER:", settings.OKTA_GROUP_VIEWER, flush=True)
            print("DEBUG OKTA_GROUP_LOADER:", settings.OKTA_GROUP_LOADER, flush=True)
            print("DEBUG OKTA_GROUP_ADMIN:", settings.OKTA_GROUP_ADMIN, flush=True)

            if "user" not in request.session:
                return redirect("/login/")

            if not check_func(request):
                print("DEBUG PERMISSION FAILED", flush=True)
                return HttpResponseForbidden("You do not have permission.")

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator