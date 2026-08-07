{{- define "palworld-selfhost.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "palworld-selfhost.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "palworld-selfhost.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "palworld-selfhost.labels" -}}
app.kubernetes.io/name: {{ include "palworld-selfhost.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "palworld-selfhost.selectorLabels" -}}
app.kubernetes.io/name: {{ include "palworld-selfhost.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "palworld-selfhost.image" -}}
{{- if .Values.image.digest }}
{{- printf "%s:%s@%s" .Values.image.repository .Values.image.tag .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end }}
{{- end }}

{{- define "palworld-selfhost.settingsSecret" -}}
{{- default (printf "%s-settings" (include "palworld-selfhost.fullname" .)) .Values.settings.existingSecret }}
{{- end }}
