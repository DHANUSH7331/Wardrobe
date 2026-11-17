from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('service-worker.js', views.service_worker, name='service_worker'),
    path('private-media/<path:name>', views.private_media, name='private_media'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(
            template_name='registration/login.html',
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/register/', views.register, name='register'),
    path('wardrobe/', views.clothing_list, name='wardrobe'),
    path('wardrobe/add/', views.clothing_add, name='clothing_add'),
    path('outfits/generate/', views.outfit_generator, name='outfit_generator'),
    path('outfits/save/', views.outfit_save, name='outfit_save'),
    path('outfits/saved/', views.saved_outfits, name='saved_outfits'),
    path(
        'outfits/<int:pk>/delete/',
        views.outfit_delete,
        name='outfit_delete',
    ),
    path(
        'outfits/<int:pk>/favorite/',
        views.outfit_favorite,
        name='outfit_favorite',
    ),
    path('wardrobe/scan/', views.scan_clothing, name='scan_clothing'),
    path(
        'wardrobe/scan/batch/',
        views.scan_batch_create,
        name='scan_batch_create',
    ),
    path(
        'wardrobe/scan/<uuid:batch_id>/upload/',
        views.scan_upload,
        name='scan_batch_upload',
    ),
    path(
        'wardrobe/scan/<uuid:batch_id>/status/',
        views.scan_status,
        name='scan_status',
    ),
    path(
        'wardrobe/scan/<uuid:batch_id>/photo/<int:photo_id>/process/',
        views.scan_process_photo,
        name='scan_process_photo',
    ),
    path(
        'wardrobe/scan/<uuid:batch_id>/photo/<int:photo_id>/image/',
        views.scan_photo_image,
        name='scan_photo_image',
    ),
    path('wardrobe/<int:pk>/', views.clothing_detail, name='clothing_detail'),
    path(
        'wardrobe/<int:pk>/edit/',
        views.clothing_edit,
        name='clothing_edit',
    ),
    path(
        'wardrobe/<int:pk>/delete/',
        views.clothing_delete,
        name='clothing_delete',
    ),
    path(
        'wardrobe/<int:pk>/favorite/',
        views.clothing_favorite,
        name='clothing_favorite',
    ),
    path(
        'wardrobe/<int:pk>/image/',
        views.clothing_image,
        name='clothing_image',
    ),
]
