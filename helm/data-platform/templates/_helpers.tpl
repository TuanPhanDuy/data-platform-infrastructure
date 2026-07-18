{{/*
Base name for a per-service resource: "<release>-<service>".
Usage: {{ include "dp.resourceName" (dict "root" $ "name" $name) }}
*/}}
{{- define "dp.resourceName" -}}
{{- printf "%s-%s" .root.Release.Name .name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* ServiceAccount name. */}}
{{- define "dp.serviceAccountName" -}}
{{- if .Values.serviceAccount.name -}}
{{- .Values.serviceAccount.name -}}
{{- else -}}
{{- printf "%s-sa" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Secret name consumed by the backends. */}}
{{- define "dp.secretName" -}}
{{- if .Values.appSecret.name -}}
{{- .Values.appSecret.name -}}
{{- else -}}
{{- printf "%s-secret" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Fully-qualified image ref for a service, honouring global.imageRegistry. */}}
{{- define "dp.image" -}}
{{- $svc := .svc -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- $tag := $svc.image.tag | default .root.Chart.AppVersion -}}
{{- if $registry -}}
{{- printf "%s%s:%s" $registry $svc.image.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $svc.image.repository $tag -}}
{{- end -}}
{{- end -}}

{{/* Common metadata labels. */}}
{{- define "dp.commonLabels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: data-platform
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Per-service selector labels. Arg: dict "root" $ "name" $name */}}
{{- define "dp.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}
