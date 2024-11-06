import asyncio
import logging
from cbpi.api import *
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Define the parameters for the plugin
@parameters([Property.Number(label="CoolerOffsetOn", configurable=True,
                             description="Offset as decimal number when the cooler is switched on."),
             Property.Number(label="CoolerOffsetOff", configurable=True,
                             description="Offset as decimal number when the cooler is switched off."),
             Property.Select(label="AutoStart", options=["Yes", "No"], description="Autostart Fermenter on cbpi start"),
             Property.Actor(label="MainCompressor", description="Primary compresor for the chiller"),
             Property.Actor(label="SecondaryCompressor", description="Secondary compresor for the chiller"),
             Property.Actor(label="ActionActuator", description="Actuator for pump and valve action"),
             Property.Fermenter(label="DependantFermenter", description="Fermenter dependency"),
             Property.Number(label="MinTempFermenter", configurable=True, description="Minimum fermenter temperatature"),
             Property.Number(label="MaxTempFermenter", configurable=True, description="Maximum fermenter temperatature"),
             Property.Number(label="MinTempChillerOffset", configurable=True, description="Chiller temperatature negative offset when fermenter is in min temperature"),
             Property.Number(label="MaxTempChillerOffset", configurable=True, description="Chiller temperatature negative offset when fermenter is in max temperature"),
             Property.Number(label="MaxTempSecondary", configurable=True, description="Chiller temperature to start secondary compressor"),
             Property.Number(label="TimeOff", configurable=True, description="Chiller time off (min)"),
             Property.Number(label="TimeOn", configurable=True, description="Chiller time on (min)")
            ])
class GlycolChillerWithDependantTargetTemperature(CBPiFermenterLogic):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
      

        # Initialize the timestamps for compressor and actuator operations
        self.compressor2_time = None
        self.actuator_last_on_time = None
        self.actuator_last_off_time = None
        self.compressor2_is_on = False

    def calculate_chiller_target(self, fermenter_target_temp):
        # Calcula la pendiente basada en los valores máximos y mínimos proporcionados
        slope = (self.max_offset_chiller - self.min_offset_chiller) / (self.max_temp_fermenter - self.min_temp_fermenter)
        unrestricted_chiller_target = slope * (fermenter_target_temp - self.min_temp_fermenter) + self.min_offset_chiller
        logger.info("[VAG]------------------> CALCULATE CHILLER2: %.2f", max(self.min_offset_chiller, min(self.max_offset_chiller, unrestricted_chiller_target)))
        return max(self.min_offset_chiller, min(self.max_offset_chiller, unrestricted_chiller_target))


    async def control_compressor1(self, compressor, chiller_temp, chiller_target_temp):
        # Control logic for the primary compressor
        if chiller_temp > chiller_target_temp:
            await self.actor_on(compressor)
        else:
            await self.actor_off(compressor)

    async def control_compressor2(self, compressor, chiller_temp, chiller_target_temp):
        # Control logic for the secondary compressor
        logger.info("[VAG]------------------> CONTROL COMP2")
        current_time = datetime.now()

        # Check if the chiller temperature is within the valid range
        if -10 <= chiller_target_temp <= 20:

            # Check if the compressor should be turned ON
            if chiller_temp >= chiller_target_temp:
                if not self.compressor2_is_on:
                    if self.compressor2_time is None or current_time - self.compressor2_time >= timedelta(minutes=25):
                        await self.actor_on(compressor)
                        self.compressor2_time = current_time
                        self.compressor2_is_on = True
                        logger.info("[VAG]--------------------------------------------------> COMP2 ON")
                elif current_time - self.compressor2_time >= timedelta(minutes=180):
                    await self.actor_off(compressor)
                    self.compressor2_time = current_time
                    self.compressor2_is_on = False
                    logger.info("[VAG]--------------------------------------------------> COMP2 OFF: timeout")

            # Check if the compressor should be turned OFF
            else:
                if self.compressor2_is_on:
                    await self.actor_off(compressor)
                    self.compressor2_time = current_time
                    self.compressor2_is_on = False
                    logger.info("[VAG]--------------------------------------------------> COMP2 OFF: temp < target - offset")

            # Log the elapsed time for diagnostics
            if self.compressor2_time is not None:
                elapsed_time = current_time - self.compressor2_time
                elapsed_seconds = elapsed_time.total_seconds()
                logger.info("[VAG]--------------------------------------------------> ELAPSED TIME: %.2f seconds", elapsed_seconds)


    async def control_action_actuator(self, chiller_temp, chiller_target_temp):
        # Control logic for the action actuator (pump + valve)
        temp_difference = chiller_temp - chiller_target_temp
        # Determine on/off times based on the temperature difference
        if temp_difference <= 0:
            on_time = timedelta(minutes=0.1)
            off_time = timedelta(minutes=0.5)
        else:
            on_time = timedelta(minutes=0.1) * (10 - min(temp_difference, 10)) / 10
            off_time = timedelta(minutes=0.5) * (1 + min(temp_difference, 10)) / 10

        # Turn the actuator on or off based on the timing
        if self.actuator_last_on_time is None or datetime.now() - self.actuator_last_on_time >= on_time:
            await self.actor_on(self.action_actuator)
            self.actuator_last_on_time = datetime.now()

        if self.actuator_last_off_time is None or datetime.now() - self.actuator_last_off_time >= off_time:
            await self.actor_off(self.action_actuator)
            self.actuator_last_off_time = datetime.now()

    async def run(self):
        try:
            logger.info("[VAG]------------------> CHILLER AUTO ON")

            self.cooler_offset_min = float(self.props.get("CoolerOffsetOn", 0))
            self.cooler_offset_max = float(self.props.get("CoolerOffsetOff", 0))

            self.compressor1 = self.props.get("MainCompressor")
            self.compressor2 = self.props.get("SecondaryCompressor")
            self.actionActor = self.props.get("ActionActuator")
            self.fermenter = self.props.get("DependantFermenter")

            self.chiller = self.get_fermenter(self.id) 

            self.min_temp_fermenter = float(self.props.get("MinTempFermenter",0))
            self.max_temp_fermenter = float(self.props.get("MaxTempFermenter",20))
            self.min_offset_chiller = float(self.props.get("MinTempChillerOffset",-6))
            self.max_offset_chiller = float(self.props.get("MaxTempChillerOffset",10))
            self.max_secondary_temp = float(self.props.get("MaxTempSecondary",10))

            while self.running == True:
                logger.info("[VAG]------------------> RUNNING ON")

                chiller_temp = float(self.get_sensor_value(self.chiller.sensor).get("value"))               
                fermenter_target_temp = float(self.get_fermenter_target_temp(self.fermenter))
                chiller_target_modified = self.calculate_chiller_target(fermenter_target_temp)

                await self.set_fermenter_target_temp(self.id, self.props.get("TargetTemp", round(chiller_target_modified, 2)))
                                
                await self.control_compressor1(self.compressor1, chiller_temp, chiller_target_modified)
                await self.control_compressor2(self.compressor2, chiller_temp, chiller_target_modified)

                await asyncio.sleep(1)

        except asyncio.CancelledError as e:
            pass
        except Exception as e:
            logging.error("Glycol Chiller Error {}".format(e))
        finally:
            self.running = False

def setup(cbpi):
    # Register the plugin with CraftBeerPi
    cbpi.plugin.register("GlycolChillerWithDependantTargetTemperature", GlycolChillerWithDependantTargetTemperature)
    pass
