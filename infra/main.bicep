targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Azure AI Foundry hub (AIServices account) name.')
@minLength(2)
@maxLength(64)
param hubName string

@description('Foundry project display name placeholder for env consistency.')
param projectName string = 'HealthcareDemo-HLS'

@description('Azure AI Search service name.')
@minLength(2)
@maxLength(60)
param searchServiceName string

@description('Fabric capacity resource name.')
@minLength(3)
@maxLength(63)
param fabricCapacityName string

@description('Fabric capacity SKU (for example F64, F256).')
param fabricCapacitySku string = 'F64'

@description('Fabric capacity admin UPN list, for example ["user@contoso.com"].')
param fabricCapacityAdmins array

@description('Tags applied to created resources.')
param tags object = {
  workload: 'hls-iq-auto-harness'
  managedBy: 'azd'
  project: projectName
}

resource aiHub 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: hubName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: hubName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
  tags: tags
}

resource aiSearch 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'enabled'
    networkRuleSet: {
      ipRules: []
    }
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
    disableLocalAuth: false
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
  tags: tags
}

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: fabricCapacityName
  location: location
  sku: {
    name: fabricCapacitySku
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: fabricCapacityAdmins
    }
  }
  tags: tags
}

output HUB_NAME string = aiHub.name
output SEARCH_SERVICE_NAME string = aiSearch.name
output FABRIC_CAPACITY_ID string = fabricCapacity.id
output FABRIC_CAPACITY_NAME string = fabricCapacity.name
output LOCATION string = location
