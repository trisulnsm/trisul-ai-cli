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
from datetime import datetime, timedelta, timezone
import random
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet
import json
import os
import re
import ast

logging.basicConfig(
    filename= Path(os.getcwd()) / "trisul_ai_cli.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)



mcp = FastMCP(name="trisul-mcp-server")

# helper function
def normalize_context(ctx: str) -> str:
    try:
        ctx = ctx.lower()
        logging.info(f"[normalize_context] Normalizing context: {ctx}")
        if ctx.startswith("context_"):
            ctx = ctx.split("_", 1)[-1]
        if ctx == "default" or ctx == "context0":
            normalized = "context0"
        else:
            normalized = f"context_{ctx}"
        logging.info(f"[normalize_context] Normalized context: {normalized}")
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
                logging.info(f"[countergroup_info] Initializing ZMQ context and socket")
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
                        logging.info("[countergroup_info] Socket closed")
                    except Exception as e:
                        logging.warning(f"Error closing socket: {str(e)}")
                if context_zmq:
                    try:
                        context_zmq.term()
                        logging.info("[countergroup_info] ZMQ context terminated")
                    except Exception as e:
                        logging.warning(f"[countergroup_info] Error terminating ZMQ context: {str(e)}")
        
        # helper
        def unwrap_response(data):
            try:
                logging.info("[countergroup_info] Unwrapping response")
                resp = trp_pb2.Message()
                resp.ParseFromString(data)
                for x in resp.DESCRIPTOR.enum_types:
                    name = x.values_by_number.get(int(resp.trp_command)).name
                logging.info(f"[countergroup_info] Response command: {name}")
                return {
                    'COUNTER_GROUP_INFO_RESPONSE': resp.counter_group_info_response
                }.get(name, resp)
            except Exception as e:
                logging.error(f"[countergroup_info] Error unwrapping response: {str(e)}")
                raise
        
        # to retrieve all counter groups send an empty COUNTER_GROUP_INFO_REQUEST
        try:
            logging.info("[countergroup_info] Building COUNTER_GROUP_INFO_REQUEST")
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


def epoch_to_duration(from_ts, to_ts):
    
    secs = int(to_ts) - int(from_ts)
    
    lookup = {"days": "Day", "hours": "Hr", "minutes": "Min", "seconds": "Sec"}
    
    secs = int(secs)
    if secs == 0:
        return "<1s"
    
    duration = ""
    
    # Calculate days
    days = secs // 86400
    if days > 0:
        lookup["days"] = f" {lookup['days']}s " if days > 1 else f" {lookup['days']} "
        duration += f"{days}{lookup['days']}"
        secs = secs - days * 86400
    
    # Calculate hours
    hours = secs // 3600
    if hours > 0:
        lookup["hours"] = f" {lookup['hours']}s " if hours > 1 else f" {lookup['hours']} "
        duration += f"{hours}{lookup['hours']}"
        secs = secs - hours * 3600
    
    # Calculate minutes
    minutes = secs // 60
    if minutes > 0:
        lookup["minutes"] = f" {lookup['minutes']}s " if minutes > 1 else f" {lookup['minutes']} "
        duration += f"{minutes}{lookup['minutes']}"
        secs = secs - minutes * 60
    
    # Remaining seconds
    if secs > 0:
        lookup["seconds"] = f" {lookup['seconds']}s " if secs > 1 else f" {lookup['seconds']} "
        duration += f"{secs}{lookup['seconds']}"
    
    starting_time = datetime.fromtimestamp(int(from_ts), timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S %z IST")
    
    return f"Duration {duration.strip()} starting from {starting_time}"






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
        logging.info(f"[list_all_available_counter_groups] Processing {len(group_details)} counter groups")
        
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
        logging.info(f"[get_cginfo_from_countergroup_name] Normalized search name: {normalized_search_name}")
        
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
                logging.info("[get_counter_group_topper] Initializing ZMQ context and socket for get_response")
                zmq_context = zmq.Context()
                socket = zmq_context.socket(zmq.REQ)
                socket.connect(zmq_endpoint)
                logging.info("[get_counter_group_topper] Sending request")
                socket.send(req.SerializeToString())
                logging.info("[get_counter_group_topper] Waiting for response")
                data = socket.recv()
                logging.info(f"[get_counter_group_topper] Received response, size: {len(data)} bytes")
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
                        logging.info("[get_counter_group_topper] Socket closed")
                    except Exception as e:
                        logging.warning(f"[get_counter_group_topper] Error closing socket: {str(e)}")
                if zmq_context:
                    try:
                        zmq_context.term()
                        logging.info("[get_counter_group_topper] ZMQ context terminated")
                    except Exception as e:
                        logging.warning(f"[get_counter_group_topper] Error terminating ZMQ context: {str(e)}")

        def unwrap_response(data):
            try:
                logging.info("[get_counter_group_topper] Unwrapping response")
                resp = trp_pb2.Message()
                resp.ParseFromString(data)
                for x in resp.DESCRIPTOR.enum_types:
                    name = x.values_by_number.get(int(resp.trp_command)).name
                logging.info(f"[get_counter_group_topper] Response command: {name}")
                return {
                    'TIMESLICES_RESPONSE': resp.time_slices_response,
                    'COUNTER_GROUP_TOPPER_RESPONSE': resp.counter_group_topper_response
                }.get(name, resp)
            except Exception as e:
                logging.error(f"[get_counter_group_topper] Error unwrapping response: {str(e)}")
                raise

        # Step 1: Get available timeslices
        logging.info("[get_counter_group_topper] Step 1: Getting available timeslices")
        req = trp_pb2.Message()
        req.trp_command = req.TIMESLICES_REQUEST
        req.time_slices_request.get_total_window = True
        resp = get_response(req)
        logging.info("[get_counter_group_topper] Timeslices received")

        # Step 2: Build topper request
        logging.info("[get_counter_group_topper] Step 2: Building topper request")
        req = trp_pb2.Message()
        req.trp_command = req.COUNTER_GROUP_TOPPER_REQUEST
        req.counter_group_topper_request.counter_group = counter_group_guid
        req.counter_group_topper_request.meter = meter
        req.counter_group_topper_request.maxitems = max_count

        # Step 3: Time interval for last duration_secs
        logging.info("[get_counter_group_topper] Step 3: Setting time interval")
        tm = trp_pb2.TimeInterval()
        tm.to.tv_sec = resp.total_window.to.tv_sec
        object = getattr(tm, 'from')
        object.tv_sec = tm.to.tv_sec - duration_secs
        req.counter_group_topper_request.time_interval.MergeFrom(tm)
        logging.info(f"[get_counter_group_topper] Time interval: from={object.tv_sec}, to={tm.to.tv_sec}")

        # Step 4: Get topper response
        logging.info("[get_counter_group_topper] Step 4: Getting topper response")
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
                logging.info("[get_key_traffic_data] Initializing ZMQ context and socket for get_response")
                #zmq send
                zmq_context = zmq.Context()
                socket = zmq_context.socket(zmq.REQ)
                socket.connect(zmq_endpoint)
                logging.info(f"[get_key_traffic_data] Connected to the socket {zmq_endpoint}")
                socket.send(req.SerializeToString())
                logging.info("[get_key_traffic_data] Request sent to socket")

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
                        logging.info("[get_key_traffic_data] Socket closed")
                    except Exception as e:
                        logging.warning(f"[get_key_traffic_data] Error closing socket: {str(e)}")

        def unwrap_response(data):
            try:
                logging.info("[get_key_traffic_data] Unwrapping response data")
                resp = trp_pb2.Message()
                resp.ParseFromString(data)
                for x in resp.DESCRIPTOR.enum_types:
                    name = x.values_by_number.get(int(resp.trp_command)).name
                logging.info(f"[get_key_traffic_data] Response command type: {name}")
                return {
                    'TIMESLICES_RESPONSE': resp.time_slices_response,
                    'COUNTER_ITEM_RESPONSE': resp.counter_item_response
                }.get(name, resp)
            except Exception as e:
                logging.error(f"[get_key_traffic_data] Error unwrapping response: {str(e)}")
                raise

        #Construct time request
        try:
            logging.info("[get_key_traffic_data] Constructing TIMESLICES_REQUEST")
            req = trp_pb2.Message()
            req.trp_command = req.TIMESLICES_REQUEST
            req.time_slices_request.get_total_window = True
            logging.info("[get_key_traffic_data] Sending TIMESLICES_REQUEST")
            tint_resp = get_response(zmq_endpoint, req)
            logging.info("[get_key_traffic_data] Received timeslices response")
        except Exception as e:
            logging.error(f"[get_key_traffic_data] Error getting timeslices: {str(e)}")
            raise


        #construct counter item request request for internal host
        try:
            logging.info("[get_key_traffic_data] Constructing COUNTER_ITEM_REQUEST")
            req = trp_pb2.Message()
            req.trp_command = req.COUNTER_ITEM_REQUEST
            req.counter_item_request.counter_group = counter_group
            req.counter_item_request.key.label = readable.lower()
            logging.info(f"[get_key_traffic_data] Counter item request configured: counter_group={counter_group}, readable={readable}")
        except Exception as e:
            logging.error(f"[get_key_traffic_data] Error constructing counter item request: {str(e)}")
            raise

        #construct time interval for last 1 hour
        try:
            logging.info("[get_key_traffic_data] Constructing time interval")
            tm = trp_pb2.TimeInterval()
            tm.MergeFrom(tint_resp.total_window)
            object = getattr(tm, 'from')
            object.tv_sec = tm.to.tv_sec - duration_secs
            
            logging.info(f"[get_key_traffic_data] Default time interval: from={object.tv_sec}, to={tm.to.tv_sec}")

            #assign time interval to counter group topper request
            if start_ts and end_ts:
                logging.info(f"[get_key_traffic_data] Overriding time interval with start_ts={start_ts}, end_ts={end_ts}")
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
        
        logging.info("[get_key_traffic_data] Sending COUNTER_ITEM_REQUEST")
        resp = get_response(zmq_endpoint, req)
        logging.info("[get_key_traffic_data] Successfully received key traffic response")
        
        result = MessageToDict(resp)
        logging.info(f"[get_key_traffic_data] Response converted to dict, keys: {result.keys()}")
        
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
                logging.info("[get_key_traffic_data] Final socket cleanup")
            except Exception as e:
                logging.warning(f"[get_key_traffic_data] Error in final socket cleanup: {str(e)}")
        if zmq_context:
            try:
                zmq_context.term()
                logging.info("[get_key_traffic_data] Final ZMQ context cleanup")
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
        logging.info("[create_crosskey_counter_group] Database connection established")
        
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

        logging.info(f"[create_crosskey_counter_group] Executing counter group insert with values: {cg_values}")
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
        
        logging.info(f"[create_crosskey_counter_group] Executing crosskey insert with values: {cross_values}")
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
                logging.info("[create_crosskey_counter_group] Cursor closed")
            except Exception as e:
                logging.warning(f"[create_crosskey_counter_group] Error closing cursor: {str(e)}")
        if conn:
            try:
                conn.close()
                logging.info("[create_crosskey_counter_group] Database connection closed")
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
            logging.info("[rag_query] Gemini API configured")
            
            logging.info("[rag_query] Generating embedding for question")
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
            logging.info(f"[rag_query] ChromaDB store path: {CHROMA_STORE}")
            
            if not CHROMA_STORE.exists():
                logging.warning(f"[rag_query] ChromaDB store path does not exist: {CHROMA_STORE}")
            
            chroma_client = chromadb.PersistentClient(path=str(CHROMA_STORE))
            collection = chroma_client.get_or_create_collection("pdf_docs")
            logging.info("[rag_query] ChromaDB client initialized successfully")
        except Exception as e:
            logging.error(f"[rag_query] Error initializing ChromaDB: {str(e)}", exc_info=True)
            return f"Error: Failed to initialize ChromaDB - {str(e)}"

        try:
            logging.info("[rag_query] Querying ChromaDB collection")
            results = collection.query(
                query_embeddings=[q_emb], 
                n_results=3, 
                include=['documents', 'distances', 'embeddings']
            )
            logging.info("[rag_query] ChromaDB query completed successfully")
            logging.info(f"[rag_query] Query results structure - Keys: {results.keys()}")
            
            if 'documents' in results:
                logging.info(f"[rag_query] Number of document groups: {len(results['documents'])}")
                if results['documents']:
                    logging.info(f"[rag_query] Number of documents in first group: {len(results['documents'][0])}")
            
            if 'distances' in results:
                logging.info(f"[rag_query] Distances: {results.get('distances', [])}")
        except Exception as e:
            logging.error(f"[rag_query] Error querying ChromaDB: {str(e)}", exc_info=True)
            return f"Error: Failed to query ChromaDB - {str(e)}"

        try:
            logging.info("[rag_query] Extracting retrieved documents")
            if 'documents' not in results or not results['documents']:
                logging.warning("[rag_query] No documents found in query results")
                return "No relevant documents found in the knowledge base."
            
            retrieved_docs = results["documents"][0]
            logging.info(f"[rag_query] Retrieved {len(retrieved_docs)} documents")
            
            if not retrieved_docs:
                logging.warning("[rag_query] Retrieved documents list is empty")
                return "No relevant documents found in the knowledge base."
            
            for i, doc in enumerate(retrieved_docs):
                logging.info(f"[rag_query] Document {i+1} preview: {doc[:100]}..." if len(doc) > 100 else f"Document {i+1}: {doc}")
        except (KeyError, IndexError) as e:
            logging.error(f"[rag_query] Error extracting documents from results: {str(e)}")
            return f"Error: Failed to extract documents - {str(e)}"
        except Exception as e:
            logging.error(f"[rag_query] Unexpected error processing documents: {str(e)}", exc_info=True)
            return f"Error: Failed to process documents - {str(e)}"
        
        # Build prompt
        try:
            logging.info("[rag_query] Building context from retrieved documents")
            context = "\n".join(retrieved_docs)
            logging.info(f"[rag_query] Context built successfully, length: {len(context)} characters")
            logging.info(f"[rag_query] Context preview: {context[:200]}..." if len(context) > 200 else f"Context: {context}")
            
            return context
        except Exception as e:
            logging.error(f"[rag_query] Error building context: {str(e)}", exc_info=True)
            return f"Error: Failed to build context - {str(e)}"
            
    except Exception as e:
        logging.error(f"[rag_query] Unexpected error in rag_query: {str(e)}", exc_info=True)
        return f"Error: An unexpected error occurred - {str(e)}"
    
    

@mcp.tool()
def show_pie_chart(data, save_image: bool = False):
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
        save_image (bool): set it 'True' to save the chart as an image file and don't display it in pop-up window. set it False to display the chart in pop-up window. Default is False.

    Returns:
        dict: Status and message about the pie chart display.
    """
    logging.info(f"[show_pie_chart] Generating the pie chart for the given data")
    
        
    if isinstance(data, str):
        data = ast.literal_eval(data)  # handles single-quoted dict strings
    else:
        data = dict(data)

    data["volumes"] = [
        eval(str(v)) if isinstance(v, str) and "*" in v else v
        for v in data.get("volumes", [])
    ]


    
    if(save_image):
        file_path = f"/tmp/pie_chart_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}.png"
        logging.info(f"[show_pie_chart] save_image is set to True, so saving the chart as an image file instead of displaying it. path: {file_path}")
        return {"status": "success", "message" : f"The pie chart is saved as an image file successfully.", "file_path": file_path}
    else:
        logging.info(f"[show_pie_chart] save_image is set to False, so displaying the chart in a pop-up window.")
        return {"status": "success", "message" : f"The pie chart is displayed in the pop-up window, tell the user to kindly check that. then show this data in the table format and give a short summary about the data.", "file_path": None}



@mcp.tool()
def show_line_chart(data, save_image: bool = False):
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
        save_image (bool): set it 'True' to save the chart as an image file and don't display it in pop-up window. set it False to display the chart in pop-up window. Default is False.
    """
    
    logging.info(f"[show_line_chart] Generating the line chart for the given data")

    if(save_image):
        file_path = f"/tmp/line_chart_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}.png"
        logging.info(f"[show_line_chart] save_image is set to True, so saving the chart as an image file instead of displaying it. path: {file_path}")
        return {"status": "success", "message" : f"The line chart is saved as an image file successfully.", "file_path": file_path}
    else:
        return {"status": "success", "message" : f"The line chart is displayed in the pop-up window, tell the user to kindly check that. then show this data in the table format and give a short summary about the data.", "file_path": None}




@mcp.tool()
def get_alerts_data(
    alert_group: str,
    duration_secs: int = 3600,
    start_ts: int = None,
    end_ts: int = None,
    context: str = "context0",
    zmq_endpoint: str = None,
    maxitems: int = 100,
    group_by_fieldname: str = None,
    resolve_keys: bool = True,
    approx_count_only: bool = False,
    source_ip: str = None,
    destination_ip: str = None,
    source_port: str = None,
    destination_port: str = None,
    any_ip: str = None,
    any_port: str = None,
    ip_pair: list = None,
    sigid: str = None,
    classification: str = None,
    priority: str = None,
    aux_message1: str = None,
    aux_message2: str = None,
    message_regex: str = None,
    idlist: list = None
):
    """
    Retrieve alert telemetry from Trisul using QUERY_ALERTS_REQUEST.

    This MCP tool acts as an alert intelligence fetcher that surfaces curated alert
    records for a specified Alert Group. It supports flexible temporal scoping, granular
    field-level filtering, grouping, and regex-based matching to enable downstream
    enrichment, correlation, or analytics workloads.

    **Mandatory Requirement:**
        - `alert_group` must be a valid Trisul Alert Group GUID.
          The function will not operate if a non-GUID or malformed value is supplied.

    Parameters:
        alert_group (str): REQUIRED. Trisul Alert Group GUID. Must be a valid GUID string.
        duration_secs (int): Relative time window in seconds. Ignored if start_ts and end_ts are provided.
        start_ts (int): Start of absolute time window (epoch seconds). Must be paired with end_ts.
        end_ts (int): End of absolute time window (epoch seconds). Must be paired with start_ts.
        context (str): Trisul context identifier. Defaults to "context0".
        zmq_endpoint (str): Custom TRP ZMQ endpoint. Auto-computed if omitted.
        maxitems (int): Hard ceiling on number of alert records returned.
        group_by_fieldname (str): Field to group output by (e.g. "sigid", "source_ip").
        resolve_keys (bool): Resolve internal keys into readable fields.
        approx_count_only (bool): Return approximate counts only, without full alert details.

        # Filters (always use readable values, not internal key format)
        source_ip (str): Filter by source IP.
        destination_ip (str): Filter by destination IP.
        source_port (str): Filter by source port.
        destination_port (str): Filter by destination port.
        any_ip (str): Match either source or destination IP.
        any_port (str): Match either source or destination port.
        ip_pair (list): One or multiple [src_ip, dst_ip] filter pairs.
        sigid (str): Filter by signature ID.
        classification (str): Filter by alert classification.
        priority (str): Filter by alert priority.
        aux_message1 (str): Text match against dispatch message field 1.
        aux_message2 (str): Text match against dispatch message field 2.
        message_regex (str): Regex match on alert message payload.
        idlist (list): Retrieve specific alerts by ID.

    Returns:
        dict: Parsed Trisul alert response, including raw or grouped alert intelligence payload.

    Usage Notes:
        - Always provide a valid GUID for `alert_group` to avoid request rejection.
        - Absolute time window (start_ts/end_ts) overrides duration_secs if both provided.
        - Optimized for downstream dashboards, analytics engines, and correlation pipelines.
    """
    zmq_context = None
    socket = None

    try:
        logging.info(f"[get_alerts_data] Start | alert_group={alert_group} duration_secs={duration_secs} start_ts={start_ts} end_ts={end_ts}")

        if not zmq_endpoint:
            ctx = normalize_context(context)
            zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{ctx}/run/trp_0"
        logging.info(f"[get_alerts_data] ZMQ endpoint: {zmq_endpoint}")

        def unwrap_response(data):
            logging.info(f"[get_alerts_data] Unwrapping response ({len(data)} bytes)")
            resp = trp_pb2.Message()
            resp.ParseFromString(data)
            for x in resp.DESCRIPTOR.enum_types:
                name = x.values_by_number.get(int(resp.trp_command)).name
            logging.info(f"[get_alerts_data] Response type: {name}")
            return {
                'TIMESLICES_RESPONSE': resp.time_slices_response,
                'QUERY_ALERTS_RESPONSE': resp.query_alerts_response
            }.get(name, resp)

        def roundtrip(req):
            nonlocal zmq_context, socket
            logging.info("[get_alerts_data] Opening ZMQ socket")
            zmq_context = zmq.Context()
            socket = zmq_context.socket(zmq.REQ)
            socket.connect(zmq_endpoint)
            logging.info("[get_alerts_data] Sending request to TRP")
            socket.send(req.SerializeToString())
            data = socket.recv()
            logging.info("[get_alerts_data] Response received from TRP")
            socket.close()
            socket = None
            return unwrap_response(data)

        logging.info("[get_alerts_data] Requesting TIMESLICES window")
        req = trp_pb2.Message()
        req.trp_command = req.TIMESLICES_REQUEST
        req.time_slices_request.get_total_window = True
        tint_resp = roundtrip(req)

        tm = trp_pb2.TimeInterval()
        tm.MergeFrom(tint_resp.total_window)
        getattr(tm, 'from').tv_sec = tm.to.tv_sec - duration_secs

        if start_ts and end_ts:
            getattr(tm, 'from').tv_sec = int(start_ts)
            getattr(tm, 'to').tv_sec = int(end_ts)
            logging.info(f"[get_alerts_data] Custom time window applied: {start_ts} to {end_ts}")

        req = trp_pb2.Message()
        req.trp_command = req.QUERY_ALERTS_REQUEST
        q = req.query_alerts_request

        q.alert_group = alert_group
        q.time_interval.MergeFrom(tm)
        q.maxitems = int(maxitems)
        q.resolve_keys = bool(resolve_keys)
        q.approx_count_only = bool(approx_count_only)

        if group_by_fieldname:
            q.group_by_fieldname = group_by_fieldname
            logging.info(f"[get_alerts_data] Group by: {group_by_fieldname}")

        def set_keyt(field, val):
            if val is None:
                return
            getattr(q, field).label = str(val).lower()
            logging.info(f"[get_alerts_data] Filter applied: {field}={val}")

        set_keyt('source_ip', source_ip)
        set_keyt('destination_ip', destination_ip)
        set_keyt('source_port', source_port)
        set_keyt('destination_port', destination_port)
        set_keyt('any_ip', any_ip)
        set_keyt('any_port', any_port)
        set_keyt('sigid', sigid)
        set_keyt('classification', classification)
        set_keyt('priority', priority)

        if aux_message1:
            logging.info(f"[get_alerts_data] aux_message1={aux_message1}")
            q.aux_message1 = aux_message1
        if aux_message2:
            logging.info(f"[get_alerts_data] aux_message2={aux_message2}")
            q.aux_message2 = aux_message2
        if message_regex:
            logging.info(f"[get_alerts_data] message_regex={message_regex}")
            q.message_regex = message_regex

        if idlist:
            q.idlist.extend([str(x) for x in idlist])
            logging.info(f"[get_alerts_data] idlist count={len(idlist)}")

        if ip_pair:
            pairs = ip_pair
            if isinstance(pairs, list) and pairs and isinstance(pairs[0], str):
                pairs = [pairs]
            for p in pairs:
                if len(p) != 2:
                    logging.warning(f"[get_alerts_data] Invalid ip_pair skipped: {p}")
                    continue
                kt1 = q.ip_pair.add()
                kt1.label = str(p[0]).lower()
                kt2 = q.ip_pair.add()
                kt2.label = str(p[1]).lower()
            logging.info(f"[get_alerts_data] ip_pair count={len(pairs)}")

        logging.info("[get_alerts_data] Executing QUERY_ALERTS_REQUEST")
        resp = roundtrip(req)
        result = MessageToDict(resp)

        logging.info("[get_alerts_data] Completed successfully")
        return result

    except Exception as e:
        logging.error(f"[get_alerts_data] Error: {str(e)}", exc_info=True)
        return {"error": str(e)}

    finally:
        if socket:
            try:
                socket.close()
                logging.info("[get_alerts_data] Socket closed")
            except Exception:
                logging.warning("[get_alerts_data] Socket close failed")
        if zmq_context:
            try:
                zmq_context.term()
                logging.info("[get_alerts_data] ZMQ context terminated")
            except Exception:
                logging.warning("[get_alerts_data] ZMQ termination failed")




@mcp.tool()
def get_flows_or_sessions_data(
        session_group: str = "{99A78737-4B41-4387-8F31-8077DB917336}",
        key: str = None,
        source_ip: str = None,
        source_port: str = None,
        dest_ip: str = None,
        dest_port: str = None,
        any_ip: str = None,
        any_port: str = None,
        ip_pair: list = None,
        protocol: str = None,
        flowtag: str = None,
        nf_routerid: str = None,
        nf_ifindex_in: str = None,
        nf_ifindex_out: str = None,
        subnet_24: str = None,
        subnet_16: str = None,
        maxitems: int = 100,
        volume_filter: int = 0,
        resolve_keys: bool = True,
        outputpath: str = None,
        idlist: list = None,
        any_nf_ifindex: str = None,
        duration_secs: int = 60,
        start_ts: int = None,
        end_ts: int = None,
        context: str = "context0",
        zmq_endpoint: str = None
    ):
    """
    Unified QuerySessions API pull.

    Business Value:
        Single entry point to query Trisul sessions with multi-criteria filtering.
        All fields in QuerySessionsRequest are supported through `filters`.
        Fields provided are implicitly AND-ed, enabling precision flow slicing.

    Args:
        session_group: Session group GUID. Default is main Flow Tracker.
        key: Match a Trisul internal session key.
        source_ip, dest_ip: Match flow endpoints by IP.
        source_port, dest_port: Match L4 ports.
        any_ip, any_port: Match either source or destination.
        ip_pair: List of 2 IPs. Matches flows between the pair.
        protocol: L4 protocol (6=TCP,17=UDP,1=ICMP).
        flowtag: Match flow tag text.
        nf_routerid: NetFlow router ID.
        nf_ifindex_in, nf_ifindex_out: NetFlow interface filters.
        subnet_24, subnet_16: Match flows inside subnet ranges.
        maxitems: Max records returned. Default 200.
        volume_filter: Only return flows > X bytes.
        resolve_keys: Resolve keys to readable format.
        outputpath: Write results to hub as CSV instead of returning.
        idlist: Flow IDs to retrieve directly. Skips filters.
        any_nf_ifindex: Match IN or OUT NF interface.
        duration_secs: Time window if timestamps not provided.
        start_ts, end_ts: Epoch timestamps override duration_secs.
        context: Trisul context.
        zmq_endpoint: Custom TRP endpoint.
        
        filters (dict): Maps directly to QuerySessionsRequest fields.
                        Examples:
                        {
                            "any_ip": "10.1.1.1",
                            "source_ip": "192.168.1.5",
                            "dest_port": "443",
                            "protocol": "6",
                            "flowtag": "malware",
                            "ip_pair": ["10.1.1.1","8.8.8.8"],
                            "subnet_24": "172.16.5.0"
                        }

    Returns:
        dict: Parsed sessions response as Python dict.
    """

    zmq_context = None
    socket = None

    try:
        if not zmq_endpoint:
            context = normalize_context(context)
            zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{context}/run/trp_0"

        logging.info(f"[QuerySessions] TRP endpoint={zmq_endpoint}")

        # Helper: ZMQ send/recv
        def get_response(req):
            nonlocal zmq_context, socket
            try:
                zmq_context = zmq.Context()
                socket = zmq_context.socket(zmq.REQ)
                socket.connect(zmq_endpoint)
                socket.send(req.SerializeToString())
                data = socket.recv()
                return unwrap_response(data)
            finally:
                if socket:
                    socket.close()

        # Helper: Parse TRP response
        def unwrap_response(data):
            resp = trp_pb2.Message()
            resp.ParseFromString(data)
            for x in resp.DESCRIPTOR.enum_types:
                name = x.values_by_number.get(int(resp.trp_command)).name
            return {
                'TIMESLICES_RESPONSE': resp.time_slices_response,
                'QUERY_SESSIONS_RESPONSE': resp.query_sessions_response
            }.get(name, resp)
            
        # Step 1: Pull Time Window
        req = trp_pb2.Message()
        req.trp_command = req.TIMESLICES_REQUEST
        req.time_slices_request.get_total_window = True
        tint_resp = get_response(req)

        tm = trp_pb2.TimeInterval()
        tm.MergeFrom(tint_resp.total_window)
        
        if not start_ts or not end_ts:
            duration_secs = int(duration_secs)
            start_ts = tm.to.tv_sec - duration_secs
            end_ts = tm.to.tv_sec
        
        start_ts = int(start_ts)
        end_ts = int(end_ts)
        
        getattr(tm, 'from').tv_sec = start_ts
        getattr(tm, 'to').tv_sec = end_ts

            
            
            

        # Step 2: Build QuerySessionsRequest
        req = trp_pb2.Message()
        req.trp_command = req.QUERY_SESSIONS_REQUEST
        q = req.query_sessions_request

        q.session_group = session_group
        q.time_interval.MergeFrom(tm)
        q.maxitems = maxitems
        q.volume_filter = volume_filter
        q.resolve_keys = resolve_keys
        if outputpath: q.outputpath = outputpath

        if key: q.key = key
        if source_ip: q.source_ip.label = source_ip
        if source_port: q.source_port.label = source_port
        if dest_ip: q.dest_ip.label = dest_ip
        if dest_port: q.dest_port.label = dest_port
        if any_ip: q.any_ip.label = any_ip
        if any_port: q.any_port.label = any_port
        if protocol: q.protocol.label = protocol
        if flowtag: q.flowtag = flowtag
        if nf_routerid: q.nf_routerid.label = nf_routerid
        if nf_ifindex_in: q.nf_ifindex_in.label = nf_ifindex_in
        if nf_ifindex_out: q.nf_ifindex_out.label = nf_ifindex_out
        if subnet_24: q.subnet_24 = subnet_24
        if subnet_16: q.subnet_16 = subnet_16
        if any_nf_ifindex: q.any_nf_ifindex.label = any_nf_ifindex

        if ip_pair and len(ip_pair) == 2:
            p1 = q.ip_pair.add(); p1.label = ip_pair[0]
            p2 = q.ip_pair.add(); p2.label = ip_pair[1]

        if idlist:
            for fid in idlist:
                q.idlist.append(fid)

        logging.info(f"[QuerySessions] Executing QuerySessions with provided filters")
        
        resp = MessageToDict(get_response(req))
        
        resp["sessions"][:] = [
            s for s in resp.get("sessions", [])
            if int(s["timeInterval"]["from"]["tvSec"]) <= end_ts and int(s["timeInterval"]["to"]["tvSec"]) >= start_ts
        ][-maxitems:]
        
        return resp
        
    except Exception as e:
        logging.error(f"[QuerySessions] Exception: {e}")
        return {"error": str(e)}

    finally:
        if zmq_context:
            zmq_context.term()




@mcp.tool()
def generate_trisul_report(pages, filename: str, report_title: str, from_ts, to_ts):
    """
    Generate a multi-page PDF report with multiple tables or  traffic charts (one per page).
    
    Args:
        filename (str): Output PDF file name.
        pages (list[dict]): Each dict = {'title': str, 'subtitle': str, 'data': list[list[str]]}
            Each page should have a title, subtitle, and data.  Data can be either a table or a chart.
            For table pages, 'data' is a 2D list representing rows and columns.
            For chart pages, 'data' is a dict with 'file_path' key pointing to the chart image file.
        report_title (str): Title of the report to be displayed in the header of all pages. The title should be short and descriptive within 2-4 words.
        from_ts (int): Start timestamp of the report duration (epoch seconds).
        to_ts (int): End timestamp of the report duration (epoch seconds).

    Example:
        pages = [
            {
                "type": "table",
                "title": "Internal Hosts",
                "subtitle": "Top internal hosts by total volume",
                "data": [
                    ["Internal Hosts", "Readable", "Flows", "Sent Bytes", "Received Bytes", "Total Bytes", "Percent"],
                    ["10.40.16.100", "10.40.16.100", "40", "49.61 K", "334.92 K", "384.53 K", "63.5"],
                    ["10.40.16.223", "10.40.16.223", "16", "28.98 K", "177.92 K", "206.90 K", "34.2"],
                ]
            },
            {
                "type": "chart",
                "title": "HTTPS Traffic Chart",
                "subtitle": "Showing HTTPS traffic trend over time",
                "file_path": "/tmp/traffic_chart_12345.png"
            },
    
        ]
        filename = 'trisul_https_report.pdf'
        report_title = 'HTTPS Traffic Report'
        from_ts = 1676610900
        to_ts = 1676614500
    """
    
    styles = getSampleStyleSheet()
    title_style = styles["Heading2"]
    title_style.leftIndent = 0
    title_style.spaceAfter = 10

    subtitle_style = styles["Heading5"]
    subtitle_style.leftIndent = 0
    subtitle_style.spaceAfter = 20
    subtitle_style.textColor = colors.HexColor("#800080")


    filename = f"/tmp/{filename}"
    
    if isinstance(pages, str):
        pages = ast.literal_eval(pages)
    
    pdf = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=10,
        rightMargin=15,
        topMargin=55,
        bottomMargin=70,
    )

    # Header/footer rendering
    def draw_header_footer(canvas, doc):
        width, height = A4

        # Header separator
        canvas.setStrokeColor(colors.black)
        canvas.line(15, height - 65, width - 15, height - 65)
        
        logo_path = Path(__file__).resolve().parent / "assets/logo_tlhs.png"
        
        duration_string = epoch_to_duration(from_ts, to_ts)
        
        
        # Logo
        try:
            canvas.drawImage(logo_path, 14, height - 63, width=69, height=49, mask='auto')
        except:
            pass

        # Header text
        canvas.setFillColorRGB(0, 0, 0)
        canvas.setFont("Helvetica", 14)
        canvas.drawRightString(width - 15, height - 28, report_title)
        canvas.setFont("Helvetica", 10)
        canvas.drawRightString(width - 15, height - 44, duration_string)
        canvas.drawRightString(width - 15, height - 58, f"Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} +05:30")

        # Footer line and text
        canvas.line(15, 43, width - 15, 43)
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.black)
        canvas.drawString(15, 30, "ACME Inc")
        canvas.drawCentredString(width / 2, 30, f"Page {doc.page}")
        canvas.drawRightString(width - 15, 30, "Generated by Trisul Network Analytics (AI Edition)")

    # Shared table style
    base_table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2880BA")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ])

    elements = []
    for i, page in enumerate(pages):
        page_type = page.get("type", "")
        title = page.get("title", "")
        subtitle = page.get("subtitle", "")

        # Add titles
        elements.append(Spacer(1, 5))
        elements.append(Paragraph(title, title_style))
        elements.append(Paragraph(f"<font color='#800080'>{subtitle}</font>", subtitle_style))
        elements.append(Spacer(1, 10))

        if page_type == "table":
            data = page.get("data", [])
            if not data:
                elements.append(Paragraph("<i>No table data available.</i>", styles["Normal"]))
            else:
                table = Table(
                    data,
                    repeatRows=1,
                    colWidths=[1.5*inch, 1.3*inch, 0.8*inch, 1.1*inch, 1.1*inch, 1.1*inch, 0.8*inch],
                )
                table.setStyle(base_table_style)
                elements.append(table)

        elif page_type == "chart":
            MAX_WIDTH = 6.5 * inch
            MAX_HEIGHT = 4.0 * inch
            

            image_path = page.get("file_path")
            if image_path:
                try:
                    # Read original image size
                    img_reader = ImageReader(image_path)
                    orig_w, orig_h = img_reader.getSize()

                    # Compute scale factor while preserving aspect ratio
                    scale_w = MAX_WIDTH / orig_w
                    scale_h = MAX_HEIGHT / orig_h
                    scale = min(scale_w, scale_h)

                    # Apply scaled dimensions
                    new_w = orig_w * scale
                    new_h = orig_h * scale

                    img = Image(image_path, width=new_w, height=new_h)
                    elements.append(img)
        
        
    
                except Exception as e:
                    elements.append(Paragraph(f"<i>Failed to load chart: {e}</i>", styles["Normal"]))
            else:
                elements.append(Paragraph("<i>No chart image path provided.</i>", styles["Normal"]))

        # Add page break except for the last page
        if i < len(pages) - 1:
            elements.append(PageBreak())

    pdf.build(elements, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)


    logging.info(f"[generate_trisul_report] PDF report generated at {filename}")

    return {"status": "success", "message" : f"The PDF report is generated successfully at {filename}. The report is displayed in the pop-up window, tell the user to kindly check that.", "file_path": filename}



@mcp.tool()
def manage_model_version():
    """
    Manage and switch between different AI model versions or LLMs for Trisul AI integrations.
    Usage:
        This tool allows administrators to manage and switch between different AI model versions
        used in Trisul AI integrations. It provides functionalities to list available models,
        set the active model version, and retrieve information about the current model in use.
    
    Returns:
        str: Confirmation message about the model management action performed.
    """
    
    logging.info(f"[manage_model_version] Managing AI model versions for Trisul AI integrations")

    return {"status": "success", "message" : f"The AI model version has been changed successfully."}




@mcp.tool()
def change_api_key():
    """
        Change the API key used for AI model integrations in Trisul AI.
        Usage:
            This tool allows administrators to change the API key used for AI model integrations
            in Trisul AI. It ensures that the new API key is securely updated and validated.

        Returns:
            str: Confirmation message about the API key change.
    """
    
    logging.info(f"[change_api_key] Changing the API key for AI model integrations in Trisul AI")
    
    return {"status": "success", "message" : f"The API key has been changed successfully."}







if __name__ == "__main__":
    mcp.run(transport="stdio")
