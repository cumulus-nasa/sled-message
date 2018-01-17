import json
import re
import sys
from datetime import datetime, date, time
import uuid
import boto3
from jsonpath_ng import jsonpath, parse

class message:

  def __getSfnExecutionArnByName(self, stateMachineArn, executionName):
    """
    * Given a state machine arn and execution name, returns the execution's ARN
    * @param {string} stateMachineArn The ARN of the state machine containing the execution
    * @param {string} executionName The name of the execution
    * @returns {string} The execution's ARN
    """
    return (':').join(stateMachineArn.replace(':stateMachine:', ':execution:'), executionName);

  def __getTaskNameFromExecutionHistory(self, executionHistory, arn):
    """
    * Given an execution history object returned by the StepFunctions API and an optional Activity
    * or Lambda ARN returns the most recent task name started for the given ARN, or if no ARN is
    * supplied, the most recent task started.
    *
    * IMPORTANT! If no ARN is supplied, this message assumes that the most recently started execution
    * is the desired execution. This WILL BREAK parallel executions, so always supply this if possible.
    *
    * @param {dict} executionHistory The execution history returned by getExecutionHistory, assumed
    *                             to be sorted so most recent executions come last
    * @param {string} arn An ARN to an Activity or Lambda to find. See "IMPORTANT!"
    * @throws If no matching task is found
    * @returns {string} The matching task name
    """
    eventsById = {};

    # Create a lookup table for finding events by their id
    for event in executionHistory['events']:
      eventsById['event']['id'] = event;

    for step in executionHistory['events']:
      # Find the ARN in thie history (the API is awful here).  When found, return its
      # previousEventId's (TaskStateEntered) name
      if (arn and
          ((step['type'] == 'LambdaFunctionScheduled' and
            step['lambdaFunctionScheduledEventDetails']['resource'] == arn) or
          (step['type'] == 'ActivityScheduled' and
            step['activityScheduledEventDetails']['resource'] == arn))):
        return eventsById[step['previousEventId']]['stateEnteredEventDetails']['name'];
      elif step['type'] == 'TaskStateEntered': return step['stateEnteredEventDetails']['name'];
    raise LookupError('No task found for ' + arn);

  def __getCurrentSfnTask(self, stateMachineArn, executionName, arn):
    """
    * Given a state machine ARN, an execution name, and an optional Activity or Lambda ARN returns
    * the most recent task name started for the given ARN in that execution, or if no ARN is
    * supplied, the most recent task started.
    *
    * IMPORTANT! If no ARN is supplied, this message assumes that the most recently started execution
    * is the desired execution. This WILL BREAK parallel executions, so always supply this if possible.
    *
    * @param {string} stateMachineArn The ARN of the state machine containing the execution
    * @param {string} executionName The name of the step function execution to look up
    * @param {string} arn An ARN to an Activity or Lambda to find. See "IMPORTANT!"
    * @returns {string} The name of the task being run
    """
    sfn = boto3.client('stepfunctions')
    executionArn = __getSfnExecutionArnByName(stateMachineArn, executionName);
    executionHistory = client.get_execution_history(
      executionArn=executionArn,
      maxResults=40,
      reverseOrder=True
    );
    __getTaskNameFromExecutionHistory(executionHistory, arn);


  ##################################
  #  Input message interpretation  #
  ##################################

  # Events stored externally

  def loadRemoteEvent(self, event):
    """
    * Looks at a Cumulus message. If the message has part of its data stored remotely in
    * S3, fetches that data and returns it, otherwise it just returns the full message
    * @param {*} event The input Lambda event in the Cumulus message protocol
    * @returns {Promise} Promise that resolves to the full event data
    """
    if ('replace' in event):
      s3 = boto3.resource('s3');
      data = s3.Object(event['replace']['Bucket'], event['replace']['Key']).get();
      if (data is not None):
        return data['Body'].read();
    return event;

  # Loading task configuration from workload template

  def __getConfig(self, event, taskName):
    """
    * Returns the configuration for the task with the given name, or an empty object if no
    * such task is configured.
    * @param {*} event An event in the Cumulus message format with remote parts resolved
    * @param {*} taskName The name of the Cumulus task
    * @returns {*} The configuration object
    """
    config = {};
    if ('workflow_config' in event and taskName in event['workflow_config']):
      config = event['workflow_config'][taskName];
    return config;

  def __loadLocalConfig(self,event):
    """
    * For local testing, returns the config for event.cumulus_meta.task
    * @param {*} event An event in the Cumulus message format with remote parts resolved
    * @returns {*} The task's configuration
    """
    return __getConfig(event, event['cumulus_meta']['task']);

  
  def __loadStepFunctionConfig(self, event, context):
    """
    * For StepFunctions, returns the configuration corresponding to the current execution
    * @param {*} event An event in the Cumulus message format with remote parts resolved
    * @param {*} context The context object passed to AWS Lambda or containing an activityArn
    * @returns {*} The task's configuration
    """
    meta = event['cumulus_meta'];
    arn = context['invokedFunctionArn'] if 'invokedFunctionArn' in context else context['activityArn'];
    taskName = __getCurrentSfnTask(meta['state_machine'],meta['execution_name'],arn);
    return __getConfig(event, taskName) if taskName is not None else None;

  def __loadConfig(self, event, context):
    """
    * Given a Cumulus message and context, returns the config object for the task
    * @param {*} event An event in the Cumulus message format with remote parts resolved
    * @param {*} context The context object passed to AWS Lambda or containing an activityArn
    * @returns {*} The task's configuration
    """
    source = event['cumulus_meta']['message_source'];
    if (source is None): raise LookupError('cumulus_meta requires a message_source');
    if (source == 'local'):
      return __loadLocalConfig(event);
    if (source == 'sfn'):
      return __loadStepFunctionConfig(event, context);

    raise LookupError('Unknown event source: '+ source);


  # Config templating
  def __resolvePathStr(self, event, str):
    """
    * Given a Cumulus message (AWS Lambda event) and a string containing a JSONPath
    * template to interpret, returns the result of interpreting that template.
    *
    * Templating comes in three flavors:
    *   1. Single curly-braces within a string ("some{$.path}value"). The JSONPaths
    *      are replaced by the first value they match, coerced to string
    *   2. A string surrounded by double curly-braces ("{{$.path}}").  The function
    *      returns the first object matched by the JSONPath
    *   3. A string surrounded by curly and square braces ("{[$.path]}"). The function
    *      returns an array of all object matching the JSONPath
    *
    * It's likely we'll need some sort of bracket-escaping at some point down the line
    *
    * @param {*} event The Cumulus message
    * @param {*} str A string containing a JSONPath template to resolve
    * @returns {*} The resolved object
    """
    valueRegex = '^{{(.*)}}$';
    arrayRegex = '^{\[(.*)\]}$';
    templateRegex = '{([^}]+)}';
    
    if (re.search(valueRegex, str)):
      matchData = parse(str[2:(len(str)-2)]).find(event);
      if len(matchData)>0: return matchData[0].value;
    
    if (re.search(arrayRegex, str)):
      matchData = parse(str[2:(len(str)-2)]).find(event);
      if len(matchData)>0: return [item.value for item in matchData];

    matches = re.search(templateRegex, str);
    if matches:
      matchData = parse(matches.group(0).lstrip('{').rstrip('}')).find(event);
      if len(matchData)>0: return str.replace(matches.group(0), matchData[0].value);

    raise LookupError('Could not resolve path ' + str);
    

  def __resolveConfigObject(self, event, config):
    """
    * Recursive helper for resolveConfigTemplates
    *
    * Given a config object containing possible JSONPath-templated values, resolves
    * all the values in the object using JSONPaths into the provided event.
    *
    * @param {*} event The event that paths resolve against
    * @param {*} config A config object, containing paths
    * @returns {*} A config object with all JSONPaths resolved
    """
    if isinstance(config, StringType):
      return __resolvePathStr(event, config);

    elif isinstance(config, ListType):
      for i in range(0, len(config)):
        config[i] = __resolveConfigObject(event, config[i]);
      return config;

    elif (config is not None and isinstance(config, TypeType)):
      result = {};
      for key in config.keys():
        result[key] = __resolveConfigObject(event, config[key]);
      return result;

    return config;


  def __resolveConfigTemplates(self, event, config):
    """
    * Given a config object containing possible JSONPath-templated values, resolves
    * all the values in the object using JSONPaths into the provided event.
    *
    * @param {*} event The event that paths resolve against
    * @param {*} config A config object, containing paths
    * @returns {*} A config object with all JSONPaths resolved
    """
    taskConfig = config.copy();
    del taskConfig['cumulus_message'];
    return __(event, taskConfig);

  ## Payload determination

  def __resolveInput(self, event, config):
    """
    * Given a Cumulus message and its config, returns the input object to send to the
    * task, as defined under config.cumulus_message
    * @param {*} event The Cumulus message
    * @param {*} config The config object
    * @returns {*} The object to place on the input key of the task's event
    """
    if ('cumulus_message' in config and 'input' in config['cumulus_message']):
      inputPath = config['cumulus_message']['input'];
      return __resolvePathStr(event, inputPath);
    return event.payload;

  """
  * Interprets an incoming event as a Cumulus workflow message
  *
  * @param {*} event The input message sent to the Lambda
  * @returns {Promise} A promise resolving to a message that is ready to pass to an inner task
  """
  def loadNestedEvent(self, event, context):
    config = __loadConfig(event, context);
    finalConfig = __resolveConfigTemplates(event, config);
    finalPayload = __resolveInput(event, config);
    return {
            'input': finalPayload,
            'config': finalConfig,
            'messageConfig': config['cumulus_message']
          };

  #############################
  # Output message creation #
  #############################

  def __assignOutputs(self, nestedResponse, event, messageConfig):
    """
    * Applies a task's return value to an output message as defined in config.cumulus_message
    *
    * @param {*} nestedResponse The task's return value
    * @param {*} event The output message to apply the return value to
    * @param {*} messageConfig The cumulus_message configuration
    * @returns {*} The output message with the nested response applied
    """
    result = event.copy();
    if messageConfig is not None and 'outputs' in messageConfig:
      outputs = messageConfig['outputs'];
      result.payload = {};
      for output in outputs:
        sourcePath = output['source'];
        destPath = output['destination'];
        destJsonPath = destPath[2:(len(destPath)-2)];
        value = __resolvePathStr(nestedResponse, sourcePath);
        parse(destJsonPath).update(result, value);
    else:
      result['payload'] = nestedResponse;

    return result;

  """
  * Stores part of a response message in S3 if it is too big to send to StepFunctions
  * @param {*} event The response message
  * @returns {*} A response message, possibly referencing an S3 object for its contents
  """
  def storeRemoteResponse(self, event):
    # Maximum message payload size that will NOT be stored in S3. Anything bigger will be.
    MAX_NON_S3_PAYLOAD_SIZE = 10000;
    jsonData = json.dumps(event);
    roughDataSize = len(jsonData) if event is not None else 0;

    if (roughDataSize < MAX_NON_S3_PAYLOAD_SIZE): return event;

    s3 = boto3.client('s3');
    s3Location = {
      'Bucket': event['ingest_meta']['message_bucket'],
      'Key': ('/').join(['events', str(uuid.uuid4())]),
    };
    s3Params = s3Location.copy().update({
      'Expires': datetime.utcnow() + timedelta(days=7), # Expire in a week
      'Body': jsonData if event is not None else '{}'
    });

    s3.put_object(**s3Params);

    return {
        'cumulus_meta': event['cumulus_meta'],
        'replace': s3Location
      };

  """
  * Creates the output message returned by a task
  *
  * @param {*} nestedResponse The response returned by the inner task code
  * @param {*} event The input message sent to the Lambda
  * @param {*} messageConfig The cumulus_message object configured for the task
  * @returns {Promise} A promise resolving to the output message to be returned
  """
  def createNextEvent(self, nestedResponse, event, messageConfig):
    result = __assignOutputs(nestedResponse, event, messageConfig);
    result['exception'] = 'None';
    del result['replace'];
    return storeRemoteResponse(result);

if __name__ == '__main__':
  (scriptName, functionName) = sys.argv[0:2];
  result = None;
  try:
    if (functionName == 'loadNestedEvent'):
      event = json.loads(argv[2]);
      context = json.loads(argv[3]);
      result = loadNestedEvent(event, context);
    elif (functionName == 'createNextEvent'):
      nestedResponse = json.loads(argv[2]);
      event = json.loads(argv[3]);
      messageConfig = json.loads(argv[4]);
      result = createNextEvent(nestedResponse, event, messageConfig);
    elif (functionName == 'loadRemoteEvent'):
      event = json.loads(argv[2]);
      result = loadRemoteEvent(event);
    
    if (result is not None and len(result) > 0):
      sys.stdout.write(json.dumps(result));
      sys.stdout.flush();
      sys.exit(0);
  except LookupError as le:
    sys.stderr(le);
  except:
    sys.stderr("Unexpected error:", sys.exc_info()[0]);

  sys.exit(1);
  