{{- define "cloudoptimizer-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cloudoptimizer-agent.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "cloudoptimizer-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cloudoptimizer-agent.labels" -}}
helm.sh/chart: {{ include "cloudoptimizer-agent.chart" . }}
{{ include "cloudoptimizer-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cloudoptimizer-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cloudoptimizer-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "cloudoptimizer-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "cloudoptimizer-agent.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Name of the Secret holding the API key, and the key within it.
Lets the Deployment reference one place regardless of whether the chart
created the Secret or the operator supplied their own.
*/}}
{{- define "cloudoptimizer-agent.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- include "cloudoptimizer-agent.fullname" . -}}
{{- end -}}
{{- end -}}

{{- define "cloudoptimizer-agent.secretKey" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecretKey -}}
{{- else -}}
api-key
{{- end -}}
{{- end -}}
