targetScope = 'resourceGroup'

param appName string
param location string
param image string
param registryName string
param environmentName string
param identityName string = '${appName}-runtime'
param keyVaultName string
param storageLinkName string = '${appName}data'
param foundryProjectEndpoint string
param modelDeployment string
param emailEndpoint string
param emailSender string
param speechEndpoint string
param documentIntelligenceEndpoint string
@minLength(3)
param initialAdminEmail string
param customDomainName string = ''
param customDomainCertificateId string = ''

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource pepperSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: keyVault
  name: 'melvan-auth-pepper'
}

var hasCustomDomain = !empty(customDomainName)
var publicUrl = hasCustomDomain
  ? 'https://${customDomainName}'
  : 'https://${appName}.${environment.properties.defaultDomain}'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        allowInsecure: false
        external: true
        targetPort: 8000
        traffic: [{ latestRevision: true, weight: 100 }]
        transport: 'auto'
        customDomains: hasCustomDomain ? [{
          name: customDomainName
          bindingType: 'SniEnabled'
          certificateId: customDomainCertificateId
        }] : []
      }
      registries: [{
        identity: identity.id
        server: registry.properties.loginServer
      }]
      secrets: [{
        identity: identity.id
        keyVaultUrl: pepperSecret.properties.secretUriWithVersion
        name: 'auth-pepper'
      }]
    }
    template: {
      containers: [{
        name: 'melvan'
        image: image
        env: [
          { name: 'MELVAN_ENVIRONMENT', value: 'hosted' }
          { name: 'MELVAN_DATABASE', value: '/tmp/melvan/melvan.sqlite3' }
          { name: 'MELVAN_PERSISTENT_DATABASE', value: '/var/lib/melvan/melvan.sqlite3' }
          { name: 'MELVAN_ATTACHMENT_ROOT', value: '/var/lib/melvan/attachments' }
          { name: 'MELVAN_SPEECH_MODE', value: 'azure' }
          { name: 'MELVAN_SPEECH_ENDPOINT', value: speechEndpoint }
          { name: 'MELVAN_SPEECH_LANGUAGE', value: 'en-US' }
          { name: 'MELVAN_OCR_MODE', value: 'azure' }
          { name: 'MELVAN_OCR_ENDPOINT', value: documentIntelligenceEndpoint }
          { name: 'MELVAN_OCR_API_VERSION', value: '2024-11-30' }
          { name: 'MELVAN_USERS_FILE', value: '/var/lib/melvan/users.json' }
          { name: 'MELVAN_MODEL_MODE', value: 'foundry' }
          { name: 'MELVAN_FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
          { name: 'MELVAN_MODEL', value: modelDeployment }
          { name: 'MELVAN_AUTH_MODE', value: 'managed_identity' }
          { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          { name: 'MELVAN_AUTH_PEPPER', secretRef: 'auth-pepper' }
          { name: 'MELVAN_INITIAL_ADMIN_EMAIL', value: initialAdminEmail }
          { name: 'MELVAN_EMAIL_MODE', value: 'azure' }
          { name: 'MELVAN_EMAIL_ENDPOINT', value: emailEndpoint }
          { name: 'MELVAN_EMAIL_SENDER', value: emailSender }
          { name: 'MELVAN_PUBLIC_BASE_URL', value: publicUrl }
        ]
        probes: [
          {
            type: 'Liveness'
            httpGet: { path: '/api/live', port: 8000, scheme: 'HTTP' }
            initialDelaySeconds: 10
            periodSeconds: 30
          }
          {
            type: 'Readiness'
            httpGet: { path: '/api/ready', port: 8000, scheme: 'HTTP' }
            initialDelaySeconds: 10
            periodSeconds: 15
          }
        ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
        volumeMounts: [{ mountPath: '/var/lib/melvan', volumeName: 'melvan-data' }]
      }]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      volumes: [{
        name: 'melvan-data'
        storageName: storageLinkName
        storageType: 'AzureFile'
      }]
    }
  }
}

output url string = publicUrl
output appName string = app.name
