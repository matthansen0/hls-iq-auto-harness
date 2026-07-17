targetScope = 'resourceGroup'

@description('Existing Foundry hub name.')
param hubName string

@description('Existing Foundry project name.')
param projectName string

@description('Fabric connection name.')
@minLength(3)
@maxLength(35)
param connectionName string

@description('Published Fabric workspace GUID.')
param workspaceId string

@description('Published Fabric Data Agent artifact GUID.')
param dataAgentId string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: hubName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource fabricConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: connectionName
  properties: {
    authType: 'CustomKeys'
    category: 'CustomKeys'
    credentials: {
      keys: {
        'workspace-id': workspaceId
        'artifact-id': dataAgentId
      }
    }
    isSharedToAll: true
    metadata: {
      type: 'fabric_dataagent_preview'
    }
    target: '-'
  }
}

output connectionId string = fabricConnection.id
