targetScope = 'resourceGroup'

param appName string
param location string
param image string
param registryName string
param environmentName string
param identityName string = '${appName}-migration'
param keyVaultName string

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

resource migrationDatabaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: keyVault
  name: 'melvan-postgres-migration-url'
}

resource runtimeDatabaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: keyVault
  name: 'melvan-postgres-runtime-url'
}

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${appName}-migrate'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [{
        identity: identity.id
        server: registry.properties.loginServer
      }]
      secrets: [
        {
          identity: identity.id
          keyVaultUrl: migrationDatabaseSecret.properties.secretUriWithVersion
          name: 'migration-database-url'
        }
        {
          identity: identity.id
          keyVaultUrl: runtimeDatabaseSecret.properties.secretUriWithVersion
          name: 'runtime-database-url'
        }
      ]
    }
    template: {
      containers: [{
        name: 'migrate'
        image: image
        command: ['python', '-m', 'melvan.migrate', 'upgrade']
        env: [
          { name: 'MELVAN_DEPLOYMENT_PROFILE', value: 'production' }
          { name: 'MELVAN_DATABASE_URL', secretRef: 'runtime-database-url' }
          { name: 'MELVAN_MIGRATION_DATABASE_URL', secretRef: 'migration-database-url' }
        ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
}

output jobName string = migrationJob.name
