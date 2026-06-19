{{/*
Expand the name of the chart.
*/}}
{{- define "docnamer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "docnamer.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "docnamer.labels" -}}
helm.sh/chart: {{ include "docnamer.name" . }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "docnamer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "docnamer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "docnamer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Effective ingress host. Prefers the ClusterOS-published cluster domain
(global.clusterDomain, injected by Fleet via the clusteros-helm-values
ConfigMap) so the service lands at docnamer.<clusterDomain> automatically.
Falls back to the first entry of .Values.ingress.hosts for manual/non-Fleet
installs.
*/}}
{{- define "docnamer.host" -}}
{{- if .Values.global.clusterDomain -}}
{{- printf "docnamer.%s" .Values.global.clusterDomain -}}
{{- else if .Values.ingress.hosts -}}
{{- (first .Values.ingress.hosts).host -}}
{{- end -}}
{{- end }}
