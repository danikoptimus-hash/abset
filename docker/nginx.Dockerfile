# syntax=docker/dockerfile:1
# Образ reverse-proxy для ПРОДА (docker-compose.prod.yml).
#
# Зачем он вообще нужен, если в dev-режиме nginx поднимается из голого
# nginx:alpine с bind-mount конфига: на прод-VM (CLK2-ABSET-01) нет доступа в
# интернет, и docker-compose.prod.yml тянет ВСЕ образы из внутреннего Harbor.
# Bind-mount при этом остался бы завязан на файл из репозитория — а на VM
# репозиторий есть (клонируется из GitLab), так что технически сработало бы.
# Но тогда версия конфига nginx определялась бы состоянием рабочей копии, а не
# тегом образа: `git pull` менял бы поведение прокси без смены ABKIT_VERSION,
# мимо всей схемы «версия = тег образа». Поэтому шаблон запекается внутрь.
#
# Поведение идентично dev-режиму: официальный образ nginx сам прогоняет
# envsubst по /etc/nginx/templates/*.template при старте, поэтому
# ABKIT_MAX_UPLOAD_MB подставляется в рантайме, как и раньше — пересборка
# образа ради смены лимита загрузки не нужна.
FROM nginx:alpine

COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80
