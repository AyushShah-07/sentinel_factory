import json #python's built in json module--Used to convert python data into json texts
import time  #python's built in time ,In this file it is mainly used for connection timeouts.

import paho.mqtt.client as mqtt #Python library used for  implementing MQTT


class MQTTClient: #class name 
    """
    ZONE X Vision -> Mosquitto -> ESP32
    Topic:
        sentinel/hardware/command #MQTT topic (Like channel)  esp32 subscribes it
    Commands: WARNING, DANGER, CLEAR
    """

    #Constructor -It runs when u create MQTTClient object
    def __init__(self,broker_host,broker_port=1883,topic="sentinel/hardware/command",client_id="zone-x-vision",):
        self.broker_host = broker_host #Broker_host is the IP address or Hostname of Mosquitto
        self.broker_port = broker_port #if you don't provide a port, 1883 is automatically used.
        self.topic = topic #local host topic -sentinel/hardware/command
        self.client_id = client_id #Every MQTT client can have a client ID.,This identifies your Vision application to the broker.

        self.connected = False #Initially We are not connected.

        self.client = mqtt.Client(client_id=self.client_id) #create the actual Paho MQTT client with self.client_id
        #MQTTClient class is our abstraction,Paho is the actual MQTT implementation.

        self.client.on_connect = self._on_connect #Telling Paho--When a connection happens, call my _on_connect() function.
        self.client.on_disconnect = self._on_disconnect#Telling Paho--When disconnection happens, call my _on_disconnect() function.

    # ==========================================================
    # CONNECT CALLBACK
    # ==========================================================

    def _on_connect( self, #This function is called by Paho when connection processing happens.
                     client,#The Paho client object.
                     userdata, #Optional user data.
                       flags, #Connection flags provided by MQTT.
                         rc,): #Return code.This tells you whether connection succeeded.
        if rc == 0:
            self.connected = True

            print("[MQTT] CONNECTED to {}:{}".format( self.broker_host, self.broker_port,))

        else:

            self.connected = False

            print("[MQTT] CONNECT FAILED rc={}".format(rc))

    # ==========================================================
    # DISCONNECT CALLBACK
    # ==========================================================

    def _on_disconnect(self,client, userdata,rc, ): #Paho automatically calls this when the MQTT connection is lost or disconnected.
        self.connected = False

        print( "[MQTT] DISCONNECTED rc={}".format(rc))
        #In Paho MQTT, rc == 0 means successful connection.

    # ==========================================================
    # CONNECT
    # ==========================================================

    def connect(self): #Try to connect Vision to Mosquitto.,we call it explicitly
        try:
            print( "[MQTT] Connecting to {}:{}...".format( self.broker_host,self.broker_port, ))

            self.client.connect(self.broker_host,self.broker_port,
                                keepalive=60,) #This means the MQTT connection uses a 60-second keepalive interval.

            # Keep MQTT network processing alive
            #MQTT needs a network-processing loop.
            self.client.loop_start() #loop_start() runs that loop in a background thread.

            deadline = time.time() + 5.0 #we give MQTT five seconds to confirm the connection.

            while (not self.connected      #Keep waiting while MQTT hasn't confirmed the connection AND we haven't exceeded 5 seconds.
and time.time() < deadline
            ):
                time.sleep(0.05) #Wait 50 milliseconds before checking again.

            if not self.connected:

                print("[MQTT] Connection was not confirmed.")

                return False

            print("[MQTT] Connection confirmed.")

            return True

        except Exception as exc:

            self.connected = False

            print("[MQTT] Connection error: {}".format(exc))

            return False

    # ==========================================================
    # ENSURE CONNECTION
    # ==========================================================

    def ensure_connected(self): #"Before sending a command, confirm that  are we connected?"

        if self.connected:
            return True

        print("[MQTT] Connection lost. Reconnecting...")

        try:

            self.client.reconnect()

            deadline = time.time() + 5.0

            while (
                not self.connected
                and time.time() < deadline
            ):
                time.sleep(0.05)

        except Exception as exc:

            print("[MQTT] Reconnect failed: {}".format(exc))

            self.connected = False

        return self.connected

    # ==========================================================
    # PUBLISH COMMAND
    # ==========================================================

    def publish_command(self, command): #This is the core function of our hardware communication.

        command = str(command).strip().upper()

        allowed_commands = {"WARNING","DANGER","CLEAR",}

        if command not in allowed_commands:

            print("[MQTT] Invalid command: {}".format(command ))

            return False

        if not self.ensure_connected():

            print("[MQTT] Cannot send {}. MQTT is not connected.".format( command) )

            return False

        payload = json.dumps( {"command": command},separators=(",", ":"),) #Build the JSON payload

        print("[MQTT] ========================================")

        print("[MQTT] COMMAND : {}".format(command))

        print("[MQTT] TOPIC   : {}".format( self.topic))

        print("[MQTT] PAYLOAD : {}".format(payload ))

        print( "[MQTT] ========================================")

        try:

            result = self.client.publish( #Send this message to this MQTT topic.
                self.topic, #sentinel/hardware/command
                payload, #ex:{"command":"DANGER"}
                qos=1, #Deliver at least once.
                retain=False, #Telling mosquito not to  store this command as the retained last message.
            )
            #result.rc tells whether the publish request was successfully accepted by the MQTT client.
            if result.rc != mqtt.MQTT_ERR_SUCCESS:

                print("[MQTT] Publish failed. rc={}".format(result.rc))

                return False

            try:

                result.wait_for_publish(timeout=3) #waits up to three seconds for the message to actually be processed by the MQTT client.

            except Exception:
                pass

            if not result.is_published():

                print("[MQTT] WARNING: publish not confirmed.")

                return False

            print("[MQTT] SENT: {}".format(command))

            return True

        except Exception as exc:

            print("[MQTT] Publish exception: {}".format(exc))

            self.connected = False

            return False

    # ==========================================================
    # WARNING
    # ==========================================================

    def warning(self):

        return self.publish_command("WARNING")

    # ==========================================================
    # DANGER
    # ==========================================================

    def danger(self):

        return self.publish_command("DANGER")

    # ==========================================================
    # CLEAR
    # ==========================================================

    def clear(self):

        return self.publish_command( "CLEAR")

    # ==========================================================
    # DISCONNECT
    # ==========================================================

    def disconnect(self):

        try:

            self.client.loop_stop()

            self.client.disconnect()

            self.connected = False

            print( "[MQTT] Disconnected.")

        except Exception as exc:

            print("[MQTT] Disconnect error: {}".format(exc))