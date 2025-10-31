from mcp.server.fastmcp import FastMCP
from trisul_ai_cli import trp_pb2
import zmq
import datetime
import sqlite3
import uuid
import google.generativeai as genai
import chromadb
from google.protobuf.json_format import MessageToDict
import logging
from pathlib import Path
from datetime import datetime



logging.basicConfig(
    filename= Path(__file__).resolve().parent / "trisul_ai_cli.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)



mcp = FastMCP(name="trisul-mcp-server")

# helper function
def normalize_context(ctx: str) -> str:
    try:
        logging.debug(f"[normalize_context] Normalizing context: {ctx}")
        if ctx.startswith("context_"):
            ctx = ctx.split("_", 1)[-1]
        if ctx == "default" or ctx == "context0":
            normalized = "context0"
        else:
            normalized = f"context_{ctx}"
        logging.debug(f"[normalize_context] Normalized context: {normalized}")
        return normalized
    except Exception as e:
        logging.error(f"[normalize_context] Error normalizing context '{ctx}': {str(e)}")
        return "context0"  # Default fallback


# helper function
def countergroup_info(zmq_endpoint: str = None, context: str = "context0", get_meter_info: bool = False):
    """Fetch all counter groups information from Trisul via ZMQ for a given zmq_endpoint.
    and it will also fetch meters info for each counter group so that we can determine what each meter means and its index.
    for example if we want to get the counter group guid for "FlowIntfs" and meter index for "Received traffic" we can use this function.
    Example output:
        [{
            "guid": "{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}","name": "FlowIntfs","bucketSize": "60000","topperBucketSize": "300",
            "timeInterval": { "from": {"tvSec": "1718711400","tvUsec": "0"}, "to": {"tvSec": "1718712060","tvUsec": "0"} },
            "meters": [
                { "id": 0, "type": "VT_RATE_COUNTER", "topcount": 1000, "name": "Bps", "description": "Total", "units": "Bps" },
                { "id": 1, "type": "VT_RATE_COUNTER", "topcount": 1000, "name": "Bps", "description": "Recv", "units": "Bps" },
                { "id": 2, "type": "VT_RATE_COUNTER", "topcount": 1000, "name": "Bps", "description": "Xmit", "units": "Bps" }
            ]
        }, ...]
    
    Arguments: zmq_endpoint (str): ZMQ Endpoint
    Returns: dict: Dictionary containing counter group information.
    """
    try:
        logging.info(f"[countergroup_info] Starting countergroup_info for zmq_endpoint: {zmq_endpoint}, get_meter_info: {get_meter_info}")
        # context = normalize_context(context)
        
        # zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{context}/run/trp_0"
            
        logging.info(f"[countergroup_info] Connecting to ZMQ endpoint: {zmq_endpoint}")
        
        # helper with timeout
        def get_response(zmq_endpoint, req, timeout_ms=10000):
            context_zmq = None
            socket = None
            try:
                logging.debug(f"[countergroup_info] Initializing ZMQ context and socket")
                context_zmq = zmq.Context()
                socket = context_zmq.socket(zmq.REQ)
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
                socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
                
                socket.connect(zmq_endpoint)
                logging.info("[countergroup_info] Connected, sending request...")
                socket.send(req.SerializeToString())
                logging.info("[countergroup_info] Request sent, waiting for response...")
                
                data = socket.recv()
                logging.info(f"[countergroup_info] Response received, size: {len(data)} bytes")
                resp = unwrap_response(data)
                return resp
            except zmq.Again:
                error_msg = f"[countergroup_info] ZMQ timeout after {timeout_ms}ms - no response from {zmq_endpoint}"
                logging.error(error_msg)
                return {"error": error_msg}
            except zmq.ZMQError as e:
                error_msg = f"[countergroup_info] ZMQ error: {str(e)}"
                logging.error(error_msg)
                return {"error": error_msg}
            except Exception as e:
                error_msg = f"[countergroup_info] Unexpected error in get_response: {str(e)}"
                logging.error(error_msg)
                return {"error": error_msg}
            finally:
                if socket:
                    try:
                        socket.close()
                        logging.debug("[countergroup_info] Socket closed")
                    except Exception as e:
                        logging.warning(f"Error closing socket: {str(e)}")
                if context_zmq:
                    try:
                        context_zmq.term()
                        logging.debug("[countergroup_info] ZMQ context terminated")
                    except Exception as e:
                        logging.warning(f"[countergroup_info] Error terminating ZMQ context: {str(e)}")
        
        # helper
        def unwrap_response(data):
            try:
                logging.debug("[countergroup_info] Unwrapping response")
                resp = trp_pb2.Message()
                resp.ParseFromString(data)
                for x in resp.DESCRIPTOR.enum_types:
                    name = x.values_by_number.get(int(resp.trp_command)).name
                logging.debug(f"[countergroup_info] Response command: {name}")
                return {
                    'COUNTER_GROUP_INFO_RESPONSE': resp.counter_group_info_response
                }.get(name, resp)
            except Exception as e:
                logging.error(f"[countergroup_info] Error unwrapping response: {str(e)}")
                raise
        
        # to retrieve all counter groups send an empty COUNTER_GROUP_INFO_REQUEST
        try:
            logging.debug("[countergroup_info] Building COUNTER_GROUP_INFO_REQUEST")
            req = trp_pb2.Message()
            req.trp_command = req.COUNTER_GROUP_INFO_REQUEST
            req.counter_group_info_request.get_meter_info = get_meter_info
        except Exception as e:
            logging.error(f"[countergroup_info] Error building request: {str(e)}")
            raise
        
        logging.info("[countergroup_info] Sending COUNTER_GROUP_INFO_REQUEST...")
        resp = get_response(zmq_endpoint, req)
        
        # Check if an error occurred and return immediately
        if isinstance(resp, dict) and "error" in resp:
            logging.error(f"[countergroup_info] Aborting due to error: {resp['error']}")
            return resp


        result = MessageToDict(resp)
        logging.info(f"[countergroup_info] Received response with {len(result.get('groupDetails', []))} groups")
        return result
        
    except Exception as e:
        logging.error(f"[countergroup_info] Error in countergroup_info: {str(e)}", exc_info=True)
        return {"error": str(e), "groupDetails": []}



@mcp.tool()
def list_all_available_counter_groups(context: str = "context0", zmq_endpoint: str = None):
    """List all available counter groups from Trisul via ZMQ for a given context or the zmq_endpoint.
    Arguments: 
        context (str): Context name, should be like context_XYZ or default or context0 etc.
        zmq_endpoint (str): ZMQ endpoint in the format "tcp://<ip_address>:<port>", for example "tcp://10.16.8.44:5008". The IP address and port may vary.
    Returns: dict: Dictionary containing counter group information.
    Example: list_all_available_counter_groups("context_XYZ") or list_all_available_counter_groups("tcp://10.16.8.44:5008") -> 
        {
            "groupDetails": [
                {"guid": "{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", "name": "ABC"},
                {"guid": "{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", "name": "XYZ"},
                ...
            ]
        }
    """
    try:
        if not zmq_endpoint:
            context = normalize_context(context)
            zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{context}/run/trp_0"

        logging.info(f"[list_all_available_counter_groups] Listing all available counter groups for zmq_endpoint: {zmq_endpoint}")

        all_cgs = countergroup_info(zmq_endpoint, get_meter_info=False)
        
        if "error" in all_cgs:
            logging.error(f"[list_all_available_counter_groups] Error from countergroup_info: {all_cgs['error']}")
            return {"error": all_cgs["error"], "groupDetails": []}
        
        group_details = all_cgs.get("groupDetails", [])
        logging.debug(f"[list_all_available_counter_groups] Processing {len(group_details)} counter groups")
        
        simplified_groups = []
        for g in group_details:
            try:
                simplified_groups.append({"guid": g["guid"], "name": g["name"]})
            except KeyError as e:
                logging.warning(f"[list_all_available_counter_groups] Missing key in group details: {str(e)}, skipping group")
                continue
        
        logging.info(f"[list_all_available_counter_groups] Retrieved {len(simplified_groups)} counter groups")
        return {"groupDetails": simplified_groups}
        
    except Exception as e:
        logging.error(f"[list_all_available_counter_groups] Error in list_all_available_counter_groups: {str(e)}", exc_info=True)
        return {"error": str(e), "groupDetails": []}



@mcp.tool()
def get_cginfo_from_countergroup_name(countergroup_name: str, context: str = "context0", zmq_endpoint: str = None):
    """Fetch counter group details by counter group name from Trisul via ZMQ for a given context or the zmq_endpoint.
    and it will also fetch meters info for each counter group so that we can determine what each meter means and its index.
    for example if we want to get the counter group guid for "ABCDE" and meter index for "Received traffic" we can use this function.
    Arguments: 
        countergroup_name (str): Counter group name
        context (str): Context name, should be like context_XYZ or default or context0 etc.
        zmq_endpoint (str): ZMQ endpoint in the format "tcp://<ip_address>:<port>", for example "tcp://10.16.8.44:5008". The IP address and port may vary.
    Returns: dict: Counter Group Details . If not found, it will return the list of all available counter groups name and the guid.
    Example: get_cginfo_from_countergroup_name("ABC", "context0") -> 
        {
            "guid": "{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}","name": "ABCDE","bucketSize": "60000","topperBucketSize": "300",
            "timeInterval": { "from": {"tvSec": "1718711400","tvUsec": "0"}, "to": {"tvSec": "1718712060","tvUsec": "0"} },
            "meters": [
                { "id": 0, "type": "VT_RATE_COUNTER", "topcount": 1000, "name": "Bps", "description": "Total", "units": "Bps" },
                { "id": 1, "type": "VT_RATE_COUNTER", "topcount": 1000, "name": "Bps", "description": "Recv", "units": "Bps" },
                { "id": 2, "type": "VT_RATE_COUNTER", "topcount": 1000, "name": "Bps", "description": "Xmit", "units": "Bps" }
            ]
        }
    """
    try:
        if not zmq_endpoint:
            context = normalize_context(context)
            zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{context}/run/trp_0"
            
        logging.info(f"[get_cginfo_from_countergroup_name] Fetching counter group info for name: {countergroup_name}, zmq_endpoint: {zmq_endpoint}")
        
        # Get all counter groups (with meter info if available)
        all_cgs = countergroup_info(zmq_endpoint, get_meter_info=True)
        
        if "error" in all_cgs:
            logging.error(f"[get_cginfo_from_countergroup_name] Error from countergroup_info: {all_cgs['error']}")
            return {"name": countergroup_name, "guid": f"Error: {all_cgs['error']}"}
        
        group_details = all_cgs.get("groupDetails", [])
        logging.info(f"[get_cginfo_from_countergroup_name] Retrieved {len(group_details)} counter groups")
        
        group_names = []
        normalized_search_name = countergroup_name.lower().replace(" ", "")
        logging.debug(f"[get_cginfo_from_countergroup_name] Normalized search name: {normalized_search_name}")
        
        for group in group_details:
            try:
                group_name = group.get("name", "")
                group_names.append(group_name)
                
                normalized_group_name = group_name.lower().replace(" ", "")
                if normalized_group_name == normalized_search_name:
                    logging.info(f"[get_cginfo_from_countergroup_name] Found matching counter group: {group_name}")
                    return group  # return full raw group dict
            except Exception as e:
                logging.warning(f"[get_cginfo_from_countergroup_name] Error processing group: {str(e)}, skipping")
                continue
        
        # If not found
        logging.warning(f"[get_cginfo_from_countergroup_name] Counter group '{countergroup_name}' not found. Available groups: {group_names}")
        return {
            "name": countergroup_name,
            "guid": "Not Found",
            "available_groups": group_names
        }
            
    except Exception as e:
        logging.error(f"[get_cginfo_from_countergroup_name] Error in get_cginfo_from_countergroup_name: {str(e)}", exc_info=True)
        return {"name": countergroup_name, "guid": f"Error: {str(e)}"}
    


@mcp.tool()
def get_counter_group_topper(counter_group_guid: str, meter: int = 0, duration_secs: int = 3600, max_count: int = 10, context: str = "context0", zmq_endpoint: str = None):
    """
    Fetch the topper metrics for a given counter group and meter over the last `duration_secs` seconds.
    Arguments: 
    counter_group_guid (str): GUID of the Counter group , meter (int): Meter index, duration_secs (int): Duration in seconds, max_count (int): maximum number of toppers retrive, 
    context (str): Context name, 
    zmp_endpoint (str): ZMQ endpoint in the format "tcp://<ip_address>:<port>", for example "tcp://10.16.8.44:5008". The IP address and port may vary.
    Returns: dict: Dictionary containing topper metrics.
    Example: get_counter_group_topper("{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", 0, 3600, "context0") or
             get_counter_group_topper("{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", 0, 3600, "tcp://10.16.8.44:5008")-> 
    {'counterGroup': '{889900CC-0063-11A5-8380-FEBDBABBDBEA}', 'meter': '0', 'keys': 
    [key': '0A.19.1E.97', 'readable': '10.25.30.151', 'label': '10.25.30.151', 'description': '', 'metric': '242287', 'metricMax': '137112', 'metricMin': '105175', 'metricAvg': '121143'}, 
    {'key': '0A.1A.0C.68', 'readable': '10.26.12.104', 'label': '10.26.12.104', 'description': '', 'metric': '227337', 'metricMax': '227337', 'metricMin': '227337', 'metricAvg': '227337'}]}
    """
    
    zmq_context = None
    socket = None
    
    try:
        if not zmq_endpoint:
            context = normalize_context(context)
            zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{context}/run/trp_0"
        
        logging.info(f"[get_counter_group_topper] Fetching counter group topper: counter_group_guid={counter_group_guid}, meter={meter}, duration_secs={duration_secs}, max_count={max_count}, context={zmq_endpoint}")

        def get_response(req):
            nonlocal zmq_context, socket
            try:
                logging.debug("[get_counter_group_topper] Initializing ZMQ context and socket for get_response")
                zmq_context = zmq.Context()
                socket = zmq_context.socket(zmq.REQ)
                socket.connect(zmq_endpoint)
                logging.debug("[get_counter_group_topper] Sending request")
                socket.send(req.SerializeToString())
                logging.debug("[get_counter_group_topper] Waiting for response")
                data = socket.recv()
                logging.debug(f"[get_counter_group_topper] Received response, size: {len(data)} bytes")
                return unwrap_response(data)
            except zmq.ZMQError as e:
                logging.error(f"[get_counter_group_topper] ZMQ error in get_response: {str(e)}")
                raise
            except Exception as e:
                logging.error(f"[get_counter_group_topper] Error in get_response: {str(e)}")
                raise
            finally:
                if socket:
                    try:
                        socket.close()
                        logging.debug("[get_counter_group_topper] Socket closed")
                    except Exception as e:
                        logging.warning(f"[get_counter_group_topper] Error closing socket: {str(e)}")
                if zmq_context:
                    try:
                        zmq_context.term()
                        logging.debug("[get_counter_group_topper] ZMQ context terminated")
                    except Exception as e:
                        logging.warning(f"[get_counter_group_topper] Error terminating ZMQ context: {str(e)}")

        def unwrap_response(data):
            try:
                logging.debug("[get_counter_group_topper] Unwrapping response")
                resp = trp_pb2.Message()
                resp.ParseFromString(data)
                for x in resp.DESCRIPTOR.enum_types:
                    name = x.values_by_number.get(int(resp.trp_command)).name
                logging.debug(f"[get_counter_group_topper] Response command: {name}")
                return {
                    'TIMESLICES_RESPONSE': resp.time_slices_response,
                    'COUNTER_GROUP_TOPPER_RESPONSE': resp.counter_group_topper_response
                }.get(name, resp)
            except Exception as e:
                logging.error(f"[get_counter_group_topper] Error unwrapping response: {str(e)}")
                raise

        # Step 1: Get available timeslices
        logging.debug("[get_counter_group_topper] Step 1: Getting available timeslices")
        req = trp_pb2.Message()
        req.trp_command = req.TIMESLICES_REQUEST
        req.time_slices_request.get_total_window = True
        resp = get_response(req)
        logging.debug("[get_counter_group_topper] Timeslices received")

        # Step 2: Build topper request
        logging.debug("[get_counter_group_topper] Step 2: Building topper request")
        req = trp_pb2.Message()
        req.trp_command = req.COUNTER_GROUP_TOPPER_REQUEST
        req.counter_group_topper_request.counter_group = counter_group_guid
        req.counter_group_topper_request.meter = meter
        req.counter_group_topper_request.maxitems = max_count

        # Step 3: Time interval for last duration_secs
        logging.debug("[get_counter_group_topper] Step 3: Setting time interval")
        tm = trp_pb2.TimeInterval()
        tm.to.tv_sec = resp.total_window.to.tv_sec
        object = getattr(tm, 'from')
        object.tv_sec = tm.to.tv_sec - duration_secs
        req.counter_group_topper_request.time_interval.MergeFrom(tm)
        logging.debug(f"[get_counter_group_topper] Time interval: from={object.tv_sec}, to={tm.to.tv_sec}")

        # Step 4: Get topper response
        logging.debug("[get_counter_group_topper] Step 4: Getting topper response")
        resp = get_response(req)
        logging.info("[get_counter_group_topper] Successfully retrieved counter group topper")

        # Step 5: Return JSON-serializable dict
        return MessageToDict(resp)
    
    except Exception as e:
        logging.error(f"[get_counter_group_topper] Error in get_counter_group_topper: {str(e)}", exc_info=True)
        return {"error": str(e)}



@mcp.tool()
def get_key_traffic_data(counter_group: str, readable: str = None, duration_secs: int = 3600, start_ts: int = None, end_ts: int = None, context: str = "context0", zmq_endpoint: str = None):
    """
    Fetch the key traffic metrics for a given counter group and readable over the last `duration_secs` seconds.
    the duration_secs can be any value other than 0.
    It will return data for all meter.
    But it will not Generate the chart display the data. you need to call the next appropriate tool to do that.
    Arguments: 
        counter_group (str): Counter group GUID, readable (str): Key value, duration_secs (int): Duration in seconds, 
        context (str): Context name, 
        zmq_endpoint (str): ZMQ endpoint in the format "tcp://<ip_address>:<port>", for example "tcp://10.16.8.44:5008". The IP address and port may vary.
    Returns: dict: Dictionary containing key traffic metrics.
    always try to pass the readable value as readable format like 10.25.46.1 or https, not in key format like 0A.19.2E.01 or p-01BB.
    Example: key_traffic("{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", "163.70.151.21", 3600, "XYZ") or
        key_traffic("{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", "163.70.151.21", 1748409542, 1748412428, "XYZ") or
        key_traffic("{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}", "163.70.151.21", 1748409542, 1748412428, "tcp://10.16.8.44:5008") ->
    output:
        {
            "counterGroup": "{XXXXXXXX-XXXX-XXXXXXXX-XXXXXXXXXXXXX}",
            "key": { "key": "A3.46.97.15", "readable": "163.70.151.21", "label": "163.70.151.21", "description": ""},
            "stats": [
                {
                    "tsTvSec": "1718711760",
                    "values": [ "302793", "5328", "297465", "281", "25", "0", "0", "302793", "0", "0", "21", "0", "0", "0", "0", "0", "67", "0", "0"]
                },
                {
                    "tsTvSec": "1718711820",
                    "values": ["253915","5819","248097","246","18","0","0","253915","0","0","20","0","0","0","0","0","53","0","0" ]
                }
            ]
        }
    """
    
    zmq_context = None
    socket = None
    
    try:        
        if not zmq_endpoint:
            context = normalize_context(context)
            zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{context}/run/trp_0"
 
        logging.info(f"[get_key_traffic_data] Fetching key traffic: counter_group={counter_group}, readable={readable}, duration_secs={duration_secs}, start_ts={start_ts}, end_ts={end_ts}, zmq_endpoint={zmq_endpoint}")
                        
            
        #get the availble time from trp 
        def get_response(zmq_endpoint, req):
            nonlocal zmq_context, socket
            try:
                logging.debug("[get_key_traffic_data] Initializing ZMQ context and socket for get_response")
                #zmq send
                zmq_context = zmq.Context()
                socket = zmq_context.socket(zmq.REQ)
                socket.connect(zmq_endpoint)
                logging.info(f"[get_key_traffic_data] Connected to the socket {zmq_endpoint}")
                socket.send(req.SerializeToString())
                logging.debug("[get_key_traffic_data] Request sent to socket")

                #zmq receive
                data = socket.recv()
                logging.info(f"[get_key_traffic_data] Received data from the socket, size: {len(data)} bytes")
                resp = unwrap_response(data)
                return resp
            except zmq.ZMQError as e:
                logging.error(f"[get_key_traffic_data] ZMQ error in get_response: {str(e)}")
                raise
            except Exception as e:
                logging.error(f"[get_key_traffic_data] Error in get_response: {str(e)}")
                raise
            finally:
                if socket:
                    try:
                        socket.close()
                        logging.debug("[get_key_traffic_data] Socket closed")
                    except Exception as e:
                        logging.warning(f"[get_key_traffic_data] Error closing socket: {str(e)}")

        def unwrap_response(data):
            try:
                logging.debug("[get_key_traffic_data] Unwrapping response data")
                resp = trp_pb2.Message()
                resp.ParseFromString(data)
                for x in resp.DESCRIPTOR.enum_types:
                    name = x.values_by_number.get(int(resp.trp_command)).name
                logging.debug(f"[get_key_traffic_data] Response command type: {name}")
                return {
                    'TIMESLICES_RESPONSE': resp.time_slices_response,
                    'COUNTER_ITEM_RESPONSE': resp.counter_item_response
                }.get(name, resp)
            except Exception as e:
                logging.error(f"[get_key_traffic_data] Error unwrapping response: {str(e)}")
                raise

        #Construct time request
        try:
            logging.debug("[get_key_traffic_data] Constructing TIMESLICES_REQUEST")
            req = trp_pb2.Message()
            req.trp_command = req.TIMESLICES_REQUEST
            req.time_slices_request.get_total_window = True
            logging.debug("[get_key_traffic_data] Sending TIMESLICES_REQUEST")
            tint_resp = get_response(zmq_endpoint, req)
            logging.info("[get_key_traffic_data] Received timeslices response")
        except Exception as e:
            logging.error(f"[get_key_traffic_data] Error getting timeslices: {str(e)}")
            raise


        #construct counter item request request for internal host
        try:
            logging.debug("[get_key_traffic_data] Constructing COUNTER_ITEM_REQUEST")
            req = trp_pb2.Message()
            req.trp_command = req.COUNTER_ITEM_REQUEST
            req.counter_item_request.counter_group = counter_group
            req.counter_item_request.key.label = readable.lower()
            logging.debug(f"[get_key_traffic_data] Counter item request configured: counter_group={counter_group}, readable={readable}")
        except Exception as e:
            logging.error(f"[get_key_traffic_data] Error constructing counter item request: {str(e)}")
            raise

        #construct time interval for last 1 hour
        try:
            logging.debug("[get_key_traffic_data] Constructing time interval")
            tm = trp_pb2.TimeInterval()
            tm.MergeFrom(tint_resp.total_window)
            object = getattr(tm, 'from')
            object.tv_sec = tm.to.tv_sec - duration_secs
            
            logging.debug(f"[get_key_traffic_data] Default time interval: from={object.tv_sec}, to={tm.to.tv_sec}")

            #assign time interval to counter group topper request
            if start_ts and end_ts:
                logging.debug(f"[get_key_traffic_data] Overriding time interval with start_ts={start_ts}, end_ts={end_ts}")
                object = getattr(tm, 'from')
                object.tv_sec = start_ts
                object = getattr(tm, 'to')
                object.tv_sec = end_ts
                logging.info(f"[get_key_traffic_data] Time interval set: from={start_ts}, to={end_ts}")
            else:
                logging.info(f"[get_key_traffic_data] Time interval set: from={object.tv_sec}, to={tm.to.tv_sec} (duration: {duration_secs}s)")
                
            req.counter_item_request.time_interval.MergeFrom(tm)
        except Exception as e:
            logging.error(f"[get_key_traffic_data] Error setting time interval: {str(e)}")
            raise
        
        logging.debug("[get_key_traffic_data] Sending COUNTER_ITEM_REQUEST")
        resp = get_response(zmq_endpoint, req)
        logging.info("[get_key_traffic_data] Successfully received key traffic response")
        
        result = MessageToDict(resp)
        logging.debug(f"[get_key_traffic_data] Response converted to dict, keys: {result.keys()}")
        
        return result
    
        
    except zmq.ZMQError as e:
        logging.error(f"[get_key_traffic_data] ZMQ error in key_traffic: {str(e)}", exc_info=True)
        return {"error": f"ZMQ error: {str(e)}"}
    except Exception as e:
        logging.error(f"[get_key_traffic_data] Error in key_traffic: {str(e)}", exc_info=True)
        return {"error": str(e)}
    finally:
        if socket:
            try:
                socket.close()
                logging.debug("[get_key_traffic_data] Final socket cleanup")
            except Exception as e:
                logging.warning(f"[get_key_traffic_data] Error in final socket cleanup: {str(e)}")
        if zmq_context:
            try:
                zmq_context.term()
                logging.debug("[get_key_traffic_data] Final ZMQ context cleanup")
            except Exception as e:
                logging.warning(f"[get_key_traffic_data] Error in final ZMQ context cleanup: {str(e)}")




@mcp.tool()
def create_crosskey_counter_group( context: str = "context0", name: str = None, description: str = "No description", toppers_interval: int = 300, bucket_size: int = 60, track_hi_water: int = 500, track_lo_water: int = 100, tail_prune_factor: int = None, last_topper_bucket_ts: str = None, row_status: str = "Active", cardinality_estimate_bits: int = None, topper_traffic_only: bool = None, enable_slice_keys: int = 1, resolver_counter_guid: str = None, cross_guid1: str = None, cross_guid2: str = None, cross_guid3: str = None, balance_depth : int = None):
    """
    Create a new crosskey counter group in Trisul.
    We cannot create the crosskey with the zmq_endpoint, it require the context name.
    Arguments:
        context (str): Context name, should be like context_XYZ or default or context0 etc.
        name (str): Name of the counter group
        description (str): Description of the counter group (Default: "No description")
        topn_commit_interval_secs (int): Time interval for toppers traffic in seconds (Default: 300)
        bucket_size (int): Time Interval for key traffic in seconds (Default: 60)
        track_hi_water (int): High water mark for tracking (Default: 500)
        track_lo_water (int): Low water mark for tracking (Default: 100)
        tail_prune_factor (int): Tail prune factor (Default: None)
        row_status (str): Counter group status(enabled or disabled), e.g., "Active" (Default: "Active")
        cardinality_estimate_bits (int): Cardinality estimate bits (Default: None)
        topper_traffic_only (bool): Whether to track topper traffic only or key traffic also (Default: None)
        enable_slice_keys (bool): Whether to enable slice keys (Default: True)
        resolver_counter_guid (str): Resolver counter GUID (Default: None)
        Returns: dict: Dictionary with details of the created counter group or error message.
        """
    
    conn = None
    cursor = None
    
    try:
        logging.info(f"[create_crosskey_counter_group] Creating crosskey counter group: name={name}, context={context}")
        
        if not name:
            error_msg = "[create_crosskey_counter_group] Counter group name is required"
            logging.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        context = normalize_context(context)
        db_path = f"/usr/local/var/lib/trisul-config/domain0/{context}/profile0/TRISULCONFIG.SQDB"
        logging.info(f"[create_crosskey_counter_group] Connecting to database: {db_path}")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        logging.debug("[create_crosskey_counter_group] Database connection established")
        
        # Create the Counter Group
        cg_sql = """
            INSERT INTO TRISUL_COUNTER_GROUPS
            (CounterGUID, Name, Description, TopNCommitIntervalSecs, BucketSizeMS, TrackHiWater, TrackLoWater, TailPruneFactor, LastTopperBucketTS, RowStatus, CardinalityEstimateBits, TopperTrafficOnly, EnableSliceKeys, CreateTimestamp, ModifyTimestamp, ResolverCounterGUID)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        new_guid = f'{{{str(uuid.uuid4()).upper()}}}'
        logging.info(f"[create_crosskey_counter_group] Generated new GUID: {new_guid}")
        
        cg_values = (
            new_guid,
            name,
            description,
            toppers_interval,
            bucket_size * 1000,
            track_hi_water,
            track_lo_water,
            tail_prune_factor,
            last_topper_bucket_ts,
            row_status,
            cardinality_estimate_bits,
            topper_traffic_only,
            enable_slice_keys,
            int(datetime.datetime.now().timestamp()),
            int(datetime.datetime.now().timestamp()),
            resolver_counter_guid
        )

        logging.debug(f"[create_crosskey_counter_group] Executing counter group insert with values: {cg_values}")
        cursor.execute(cg_sql, cg_values)
        conn.commit()
        logging.info("[create_crosskey_counter_group] Counter group inserted successfully")
        
        cross_sql = """
            INSERT INTO TRISUL_COUNTER_GROUP_CROSSKEYS
            (CounterGUID, ParentCounterGUID, CrosskeyCounterGUID, CrosskeyThirdCounterGUID,
            KeyLength1, KeyLength2, KeyLength3, BalanceDepth1, BalanceDepth2)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        cross_values = (
            new_guid,
            cross_guid1,
            cross_guid2,
            cross_guid3,
            None, 
            None, 
            None, 
            balance_depth, 
            None
        )
        
        logging.debug(f"[create_crosskey_counter_group] Executing crosskey insert with values: {cross_values}")
        cursor.execute(cross_sql, cross_values)
        conn.commit()
        logging.info("[create_crosskey_counter_group] Crosskey configuration inserted successfully")
        
        success_msg = f"[create_crosskey_counter_group] Counter group '{name}' successfully created with guid {new_guid}."
        logging.info(success_msg)
        return {"status": "success", "message": success_msg}
        
    except sqlite3.IntegrityError as e:
        error_msg = f"[create_crosskey_counter_group] Database integrity error: {str(e)}"
        logging.error(error_msg)
        if conn:
            conn.rollback()
        return {"status": "error", "message": error_msg}
    except sqlite3.OperationalError as e:
        error_msg = f"[create_crosskey_counter_group] Database operational error: {str(e)}"
        logging.error(error_msg)
        return {"status": "error", "message": error_msg}
    except Exception as e:
        error_msg = f"[create_crosskey_counter_group] Error creating counter group: {str(e)}"
        logging.error(error_msg, exc_info=True)
        if conn:
            conn.rollback()
        return {"status": "error", "message": error_msg}
    finally:
        if cursor:
            try:
                cursor.close()
                logging.debug("[create_crosskey_counter_group] Cursor closed")
            except Exception as e:
                logging.warning(f"[create_crosskey_counter_group] Error closing cursor: {str(e)}")
        if conn:
            try:
                conn.close()
                logging.debug("[create_crosskey_counter_group] Database connection closed")
            except Exception as e:
                logging.warning(f"[create_crosskey_counter_group] Error closing database connection: {str(e)}")



@mcp.tool()
def rag_query(question: str):
    """
    Perform a RAG (Retrieval-Augmented Generation) query using Gemini and ChromaDB.
    It does not need any context or the zmq_endpoint
    Arguments: question (str): The question to query.
    Returns: str: The answer generated by Gemini.
        Example: rag_query("what is crosskey?") -> 
        "Crosskey is a feature in Trisul that allows you to combine multiple counter groups to create a new composite counter group. 
        For example, you can create a crosskey counter group that combines the 'Source IP' and 'Destination IP' counter groups to track traffic between specific IP pairs."
    """

    try:
        logging.info(f"[rag_query] Starting RAG query for question: {question}")
        
        # Embed query
        logging.info("[rag_query] Initializing Gemini API for embedding")
        try:
            # getting the api key
            from dotenv import dotenv_values
            from pathlib import Path
            env_path = Path(__file__).resolve().parent / ".env"
            config = dotenv_values(env_path)
            GEMINI_API_KEY = config.get("TRISUL_GEMINI_API_KEY")
            
            genai.configure(api_key=GEMINI_API_KEY)
            logging.debug("[rag_query] Gemini API configured")
            
            logging.debug("[rag_query] Generating embedding for question")
            resp = genai.embed_content(model="models/gemini-embedding-001", content=question)
            q_emb = resp['embedding']
            logging.info(f"[rag_query] Embedding generated successfully, dimension: {len(q_emb)}")
        except KeyError as e:
            logging.error(f"[rag_query] Missing key in embedding response: {str(e)}")
            return f"Error: Failed to generate embedding - {str(e)}"
        except Exception as e:
            logging.error(f"[rag_query] Error generating embedding: {str(e)}", exc_info=True)
            return f"Error: Failed to generate embedding - {str(e)}"

        # Search in Chroma
        try:
            logging.info("[rag_query] Initializing ChromaDB client")
            CHROMA_STORE = Path(__file__).resolve().parent / "chroma_store"
            logging.debug(f"[rag_query] ChromaDB store path: {CHROMA_STORE}")
            
            if not CHROMA_STORE.exists():
                logging.warning(f"[rag_query] ChromaDB store path does not exist: {CHROMA_STORE}")
            
            chroma_client = chromadb.PersistentClient(path=str(CHROMA_STORE))
            collection = chroma_client.get_or_create_collection("pdf_docs")
            logging.info("[rag_query] ChromaDB client initialized successfully")
        except Exception as e:
            logging.error(f"[rag_query] Error initializing ChromaDB: {str(e)}", exc_info=True)
            return f"Error: Failed to initialize ChromaDB - {str(e)}"

        try:
            logging.debug("[rag_query] Querying ChromaDB collection")
            results = collection.query(
                query_embeddings=[q_emb], 
                n_results=3, 
                include=['documents', 'distances', 'embeddings']
            )
            logging.info("[rag_query] ChromaDB query completed successfully")
            logging.debug(f"[rag_query] Query results structure - Keys: {results.keys()}")
            
            if 'documents' in results:
                logging.debug(f"[rag_query] Number of document groups: {len(results['documents'])}")
                if results['documents']:
                    logging.debug(f"[rag_query] Number of documents in first group: {len(results['documents'][0])}")
            
            if 'distances' in results:
                logging.debug(f"[rag_query] Distances: {results.get('distances', [])}")
        except Exception as e:
            logging.error(f"[rag_query] Error querying ChromaDB: {str(e)}", exc_info=True)
            return f"Error: Failed to query ChromaDB - {str(e)}"

        try:
            logging.debug("[rag_query] Extracting retrieved documents")
            if 'documents' not in results or not results['documents']:
                logging.warning("[rag_query] No documents found in query results")
                return "No relevant documents found in the knowledge base."
            
            retrieved_docs = results["documents"][0]
            logging.info(f"[rag_query] Retrieved {len(retrieved_docs)} documents")
            
            if not retrieved_docs:
                logging.warning("[rag_query] Retrieved documents list is empty")
                return "No relevant documents found in the knowledge base."
            
            for i, doc in enumerate(retrieved_docs):
                logging.debug(f"[rag_query] Document {i+1} preview: {doc[:100]}..." if len(doc) > 100 else f"Document {i+1}: {doc}")
        except (KeyError, IndexError) as e:
            logging.error(f"[rag_query] Error extracting documents from results: {str(e)}")
            return f"Error: Failed to extract documents - {str(e)}"
        except Exception as e:
            logging.error(f"[rag_query] Unexpected error processing documents: {str(e)}", exc_info=True)
            return f"Error: Failed to process documents - {str(e)}"
        
        # Build prompt
        try:
            logging.debug("[rag_query] Building context from retrieved documents")
            context = "\n".join(retrieved_docs)
            logging.info(f"[rag_query] Context built successfully, length: {len(context)} characters")
            logging.debug(f"[rag_query] Context preview: {context[:200]}..." if len(context) > 200 else f"Context: {context}")
            
            return context
        except Exception as e:
            logging.error(f"[rag_query] Error building context: {str(e)}", exc_info=True)
            return f"Error: Failed to build context - {str(e)}"
            
    except Exception as e:
        logging.error(f"[rag_query] Unexpected error in rag_query: {str(e)}", exc_info=True)
        return f"Error: An unexpected error occurred - {str(e)}"
    
    

@mcp.tool()
def show_pie_chart(data):
    """
    Plots a static traffic chart (pie chart) using matplotlib based on the provided JSON-like input and show it in a new pop-up window.
    
    Usage:
        To display pie chart to show the topper values for any counter group and meter.
        It does not need any context name or the zmq_endpoint.
        It can be used to display the traffic distribution for any key like IP address, protocol, port etc.

    Args:
        data (dict): Pie Chart configuration and series data.
                    Example format:
                    {
                       'chart_title': 'Top Applications by Traffic',
                       'legend_title': 'Applications',
                       'labels': ['HTTP', 'HTTPS', 'DNS', 'SSH', 'FTP'],
                       'volumes': [5776124, 4733635, 1028367, 14143, 14001],
                       'colors': ['#1F77B4', '#FF7F0E', '#2CA02C', '#D62728', '#9467BD']
                    }

    Returns:
        dict: Status and message about the pie chart display.
    """
    logging.info(f"[show_pie_chart] Generating the pie chart for the given data")

    return {"status": "success", "message" : f"The pie chart is displayed in the pop-up window, tell the user to kindly check that. then show this data in the table format and give a short summary about the data."}




@mcp.tool()
def show_line_chart(data):
    """
    Plots a static traffic chart (line chart) using matplotlib based on the provided JSON-like input and show it in a new pop-up window.
    the input values should be in raw bytes format  not in mb or kb.
    the time stamps should be in epoc seconds format as integer not in the string date time format like this '2025-10-16 01:00:00'.
    It does not need any context name or the zmq_endpoint.
    It can be used to display the traffic data for any key like IP address, protocol, port etc over a time period.
    
    Args:
        data (dict): Line Chart configuration and series data.
                     Example format:
                     {
                        "title": "Network Traffic Over 24 Hours", "x_label": "Time", "y_label": "Traffic",
                        "keys": [
                            {
                                "timestamps": [1718714100, 1718714160], "legend_label": "Upload", "color": "red", "values": [32432, 37293]
                            },
                            ...
                        ]
                     }
    """
    
    logging.info(f"[show_line_chart] Generating the line chart for the given data")

    return {"status": "success", "message" : f"The line chart is displayed in the pop-up window, tell the user to kindly check that. then show this data in the table format and give a short summary about the data."}






if __name__ == "__main__":
    mcp.run(transport="stdio")
