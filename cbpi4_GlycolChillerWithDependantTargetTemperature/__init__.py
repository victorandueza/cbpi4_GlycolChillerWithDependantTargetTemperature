import asyncio
import logging
from cbpi.api import *
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Define the parameters for the plugin
@parameters([
    Property.Number(label="HeaterOffsetOn", configurable=True, description="Offset when the heater is switched on"),
    Property.Number(label="HeaterOffsetOff", configurable=True, description="Offset when the heater is switched off"),
    Property.Number(label="CoolerOffsetOn", configurable=True, description="Offset when the cooler is switched on"),
    Property.Number(label="CoolerOffsetOff", configurable=True, description="Offset when the cooler is switched off"),
    Property.Select(label="AutoStart", options=["Yes", "No"], description="Autostart Fermenter on cbpi start"),
    Property.Sensor(label="ChillerTemperatureSensor", description="Chiller sensor"),
    Property.Actor(label="Compresor1", description="Primary compresor for the chiller"),
    Property.Actor(label="Compresor2", description="Secondary compresor for the chiller"),
    Property.Actor(label="ActionActuator", description="Actuator for pump and valve action"),
    Property.Fermenter(label="DependantFermenter", description="Fermenter dependency")
])
class GlycolChillerControl(CBPiFermenterLogic):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize variables to track the last time the compressors and actuators were switched
        self.compressor2_last_on_time = None
        self.compressor2_last_off_time = None
        self.actuator_last_on_time = None
        self.actuator_last_off_time = None

    def calculate_chiller_target(self, fermenter_target_temp):
        # Calculate the target temperature for the chiller based on the fermenter's target temperature
        return fermenter_target_temp#-6 + (fermenter_target_temp / 20) * 16

    async def control_compressor(self, compressor, chiller_temp, chiller_target_temp):
        # Control logic for the primary compressor
        if compressor == self.compresor1:
            if chiller_temp > chiller_target_temp:
                await self.actor_on(compressor)
            else:
                await self.actor_off(compressor)

        # Control logic for the secondary compressor
        elif compressor == self.compresor2:
            if -10 <= chiller_temp <= 5:
                # Check if the secondary compressor can be turned on or off based on the timing constraints
                if self.compressor2_last_on_time is None or \
                   datetime.now() - self.compressor2_last_on_time > timedelta(hours=1):
                    if self.compressor2_last_off_time is None or \
                       datetime.now() - self.compressor2_last_off_time > timedelta(minutes=15):
                        await self.actor_on(compressor)
                        self.compressor2_last_on_time = datetime.now()
                else:
                    await self.actor_off(compressor)
                    if self.compressor2_last_off_time is None:
                        self.compressor2_last_off_time = datetime.now()

    async def control_action_actuator(self, chiller_temp, chiller_target_temp):
        # Control logic for the action actuator (pump + valve)
        temp_difference = chiller_temp - chiller_target_temp
        # Determine on/off times based on the temperature difference
        if temp_difference <= 0:
            on_time = timedelta(minutes=1)
            off_time = timedelta(minutes=10)
        else:
            on_time = timedelta(minutes=1) * (10 - min(temp_difference, 10)) / 10
            off_time = timedelta(minutes=10) * (1 + min(temp_difference, 10)) / 10

        # Turn the actuator on or off based on the timing
        if self.actuator_last_on_time is None or datetime.now() - self.actuator_last_on_time >= on_time:
            await self.actor_on(self.action_actuator)
            self.actuator_last_on_time = datetime.now()

        if self.actuator_last_off_time is None or datetime.now() - self.actuator_last_off_time >= off_time:
            await self.actor_off(self.action_actuator)
            self.actuator_last_off_time = datetime.now()

    async def run(self):
        # Main loop for the plugin
        try:
            while self.running:
                # Get the target temperature of the fermenter and calculate the target temperature for the chiller
                fermenter_temp = self.props.get("DependantFermenter")
                fermenter_target_temp = float(self.get_fermenter_target_temp(fermenter_temp).get("value"))
                chiller_target_temp = self.calculate_chiller_target(fermenter_target_temp)
                # Get the current temperature of the chiller
                chiller_temp_sensor = self.props.get("ChillerTemperatureSensor")
                chiller_temp = float(self.get_sensor_value(chiller_temp_sensor).get("value"))

                # Control the compressors and the action actuator
                await self.control_compressor(self.compresor1, chiller_temp, chiller_target_temp)
                await self.control_compressor(self.compresor2, chiller_temp, chiller_target_temp)
                await self.control_action_actuator(chiller_temp, chiller_target_temp)

                await asyncio.sleep(1)

        except asyncio.CancelledError as e:
            # Handle cancellation of the task
            pass
        except Exception as e:
            # Log any unexpected errors
            logger.error("Glycol Chiller Control Error: {}".format(e))
        finally:
            # Ensure everything is turned off when the plugin stops
            self.running = False
            await self.actor_off(self.compresor1)
            await self.actor_off(self.compresor2)
            await self.actor_off(self.action_actuator)

def setup(cbpi):
    # Register the plugin with CraftBeerPi
    cbpi.plugin.register("GlycolChillerControl", GlycolChillerControl)
    pass
