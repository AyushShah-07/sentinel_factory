import json
import time

import paho.mqtt.client as mqtt


class MQTTClient:
    """
    MQTT client used to send safety events from Vision
    to the ESP32 hardware controller.
    """

    def __init__(
        self,
        broker_host="172.22.3.228",
        broker_port=1883,
        topic="sentinel/hardware/command",
        client_id="SentinelVision",
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.topic = topic
        self.client_id = client_id

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
        )

        self.connected = False

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected = True
            print(
                "[MQTT] Connected to {}:{}".format(
                    self.broker_host,
                    self.broker_port,
                )
            )
        else:
            self.connected = False
            print(
                "[MQTT] Connection failed: {}".format(
                    reason_code
                )
            )

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):
        self.connected = False

        print(
            "[MQTT] Disconnected. reason={}".format(
                reason_code
            )
        )

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------

    def connect(self):
        """
        Connect to Mosquitto without crashing the Vision pipeline.
        """

        try:
            print(
                "[MQTT] Connecting to {}:{} ...".format(
                    self.broker_host,
                    self.broker_port,
                )
            )

            self.client.connect(
                self.broker_host,
                self.broker_port,
                60,
            )

            # Background MQTT network loop.
            self.client.loop_start()

            # Give the connection a short moment to establish.
            deadline = time.time() + 3.0

            while (
                not self.connected
                and time.time() < deadline
            ):
                time.sleep(0.05)

            if not self.connected:
                print(
                    "[MQTT] Broker connection not confirmed."
                )

        except Exception as exc:
            self.connected = False

            print(
                "[MQTT] Connection error: {}".format(
                    exc
                )
            )

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, payload):
        """
        Publish a JSON payload to the configured MQTT topic.
        """

        if not self.connected:
            print(
                "[MQTT] Not connected; event not published."
            )
            return False

        try:
            message = json.dumps(
                payload,
                default=str,
            )

            result = self.client.publish(
                self.topic,
                message,
                qos=1,
                retain=False,
            )

            if result.rc == mqtt.MQTT_ERR_SUCCESS:

                print(
                    "[MQTT] Published: {}".format(
                        message
                    )
                )

                return True

            print(
                "[MQTT] Publish failed. rc={}".format(
                    result.rc
                )
            )

            return False

        except Exception as exc:

            print(
                "[MQTT] Publish exception: {}".format(
                    exc
                )
            )

            return False

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def disconnect(self):

        try:
            self.client.loop_stop()
            self.client.disconnect()

        except Exception as exc:

            print(
                "[MQTT] Disconnect error: {}".format(
                    exc
                )
            )