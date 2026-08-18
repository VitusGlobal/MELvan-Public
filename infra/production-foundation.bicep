targetScope = 'resourceGroup'

@description('Short lowercase application name used in Azure resource names.')
param appName string

@description('Azure region selected by the fork operator.')
param location string

@secure()
@minLength(32)
param authPepper string

@secure()
@minLength(16)
param postgresAdminPassword string

@secure()
@minLength(16)
param postgresRuntimePassword string

@description('Object ID of the deploying user or workload identity.')
param deploymentPrincipalId string

param postgresSkuName string = 'Standard_B1ms'
param postgresTier string = 'Burstable'
param postgresVersion string = '16'
@minValue(32)
param postgresStorageSizeGB int = 32

var suffix = uniqueString(subscription().id, resourceGroup().id)
var registryName = take(replace('${appName}${suffix}', '-', ''), 50)
var storageName = take(replace('${appName}${suffix}', '-', ''), 24)
var keyVaultName = take('${appName}-${suffix}', 24)
var environmentName = '${appName}-environment'
var workspaceName = '${appName}-logs'
var runtimeIdentityName = '${appName}-runtime'
var migrationIdentityName = '${appName}-migration'
var speechName = take('${appName}-speech-${suffix}', 64)
var documentIntelligenceName = take('${appName}-ocr-${suffix}', 64)
var postgresName = take('${appName}-pg-${suffix}', 63)
var postgresDatabaseName = 'melvan'
var postgresAdminLogin = 'melvan_migrator'
var postgresRuntimeLogin = 'melvan_app'
var blobContainerName = 'melvan-attachments'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource runtimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: runtimeIdentityName
  location: location
}

resource migrationIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: migrationIdentityName
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
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource attachmentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
  properties: { publicAccess: 'None' }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresName
  location: location
  sku: {
    name: postgresSkuName
    tier: postgresTier
  }
  properties: {
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    storage: {
      autoGrow: 'Enabled'
      storageSizeGB: postgresStorageSizeGB
    }
    version: postgresVersion
  }
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: postgresDatabaseName
  properties: {}
}

resource azureServicesFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
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

var postgresHost = postgres.properties.fullyQualifiedDomainName
var migrationDatabaseUrl = 'postgresql+psycopg://${postgresAdminLogin}:${postgresAdminPassword}@${postgresHost}:5432/${postgresDatabaseName}?sslmode=require'
var runtimeDatabaseUrl = 'postgresql+psycopg://${postgresRuntimeLogin}:${postgresRuntimePassword}@${postgresHost}:5432/${postgresDatabaseName}?sslmode=require'

resource pepperSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'melvan-auth-pepper'
  properties: { value: authPepper }
}

resource migrationDatabaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'melvan-postgres-migration-url'
  properties: { value: migrationDatabaseUrl }
}

resource runtimeDatabaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'melvan-postgres-runtime-url'
  properties: { value: runtimeDatabaseUrl }
}

var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var blobDataContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource runtimeRegistryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, runtimeIdentity.id, acrPullRole)
  properties: {
    principalId: runtimeIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource migrationRegistryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, migrationIdentity.id, acrPullRole)
  properties: {
    principalId: migrationIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole
  }
}

resource runtimePepperRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: pepperSecret
  name: guid(pepperSecret.id, runtimeIdentity.id, keyVaultSecretsUserRole)
  properties: {
    principalId: runtimeIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource runtimeDatabaseRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: runtimeDatabaseSecret
  name: guid(runtimeDatabaseSecret.id, runtimeIdentity.id, keyVaultSecretsUserRole)
  properties: {
    principalId: runtimeIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource migrationDatabaseRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: migrationDatabaseSecret
  name: guid(migrationDatabaseSecret.id, migrationIdentity.id, keyVaultSecretsUserRole)
  properties: {
    principalId: migrationIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource migrationRuntimeDatabaseRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: runtimeDatabaseSecret
  name: guid(runtimeDatabaseSecret.id, migrationIdentity.id, keyVaultSecretsUserRole)
  properties: {
    principalId: migrationIdentity.properties.principalId
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

resource blobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, runtimeIdentity.id, blobDataContributorRole)
  properties: {
    principalId: runtimeIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobDataContributorRole
  }
}

output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output environmentName string = environment.name
output identityPrincipalId string = runtimeIdentity.properties.principalId
output migrationIdentityName string = migrationIdentity.name
output keyVaultName string = keyVault.name
output storageAccountUrl string = 'https://${storage.name}.blob.core.windows.net'
output blobContainerName string = attachmentContainer.name
output speechResourceId string = speech.id
output speechEndpoint string = 'https://${speech.name}.cognitiveservices.azure.com/stt/speech/recognition/conversation/cognitiveservices/v1'
output documentIntelligenceResourceId string = documentIntelligence.id
output documentIntelligenceEndpoint string = 'https://${documentIntelligence.name}.cognitiveservices.azure.com'
