targetScope = 'resourceGroup'

@description('Short lowercase application name used in Azure resource names.')
param appName string

@description('Azure region selected by the fork operator.')
param location string

@secure()
@minLength(32)
@description('Stable deployment pepper. The deployment script retains it in Key Vault.')
param authPepper string

@description('Object ID of the deploying user or workload identity, used only to retain the existing pepper on later deployments.')
param deploymentPrincipalId string

var suffix = uniqueString(subscription().id, resourceGroup().id)
var registryName = take(replace('${appName}${suffix}', '-', ''), 50)
var storageName = take(replace('${appName}${suffix}', '-', ''), 24)
var keyVaultName = take('${appName}-${suffix}', 24)
var environmentName = '${appName}-environment'
var workspaceName = '${appName}-logs'
var identityName = '${appName}-runtime'
var storageLinkName = '${appName}data'
var fileShareName = take('${appName}-data', 63)
var speechName = take('${appName}-speech-${suffix}', 64)
var documentIntelligenceName = take('${appName}-ocr-${suffix}', 64)

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    retentionInDays: 30
    sku: { name: 'PerGB2018' }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    enabledProtocols: 'SMB'
    shareQuota: 5
  }
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: environment
  name: storageLinkName
  properties: {
    azureFile: {
      accessMode: 'ReadWrite'
      accountKey: storage.listKeys().keys[0].value
      accountName: storage.name
      shareName: fileShare.name
    }
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    enableRbacAuthorization: true
    enableSoftDelete: true
    publicNetworkAccess: 'Enabled'
    sku: { family: 'A', name: 'standard' }
    tenantId: tenant().tenantId
  }
}

resource speech 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: speechName
  location: location
  kind: 'SpeechServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: speechName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: documentIntelligenceName
  location: location
  kind: 'FormRecognizer'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: documentIntelligenceName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource pepperSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'melvan-auth-pepper'
  properties: { value: authPepper }
}

var acrPullRole = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsUserRole = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, acrPullRole)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource vaultSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, identity.id, keyVaultSecretsUserRole)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource deploymentVaultRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, deploymentPrincipalId, keyVaultSecretsUserRole)
  properties: {
    principalId: deploymentPrincipalId
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output environmentName string = environment.name
output environmentDefaultDomain string = environment.properties.defaultDomain
output identityId string = identity.id
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
output keyVaultName string = keyVault.name
output pepperSecretUri string = pepperSecret.properties.secretUriWithVersion
output storageLinkName string = environmentStorage.name
output speechResourceId string = speech.id
output speechEndpoint string = 'https://${speech.name}.cognitiveservices.azure.com/stt/speech/recognition/conversation/cognitiveservices/v1'
output documentIntelligenceResourceId string = documentIntelligence.id
output documentIntelligenceEndpoint string = 'https://${documentIntelligence.name}.cognitiveservices.azure.com'
