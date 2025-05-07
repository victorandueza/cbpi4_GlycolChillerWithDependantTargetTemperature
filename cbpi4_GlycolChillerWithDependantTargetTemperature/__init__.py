import asyncio
import logging
from cbpi.api import *
from datetime import datetime, timedelta
import pkg_resources

logger = logging.getLogger(__name__)

try:
    version = pkg_resources.get_distribution("cbpi4_GlycolChillerWithDependantTargetTemperature").version
    logger.info(f"[PLUGIN] GlycolChiller plugin cargado – versión {version}")
except Exception as e:
    logger.warning(f"[PLUGIN] No se pudo obtener la versión del plugin: {e}")

@parameters([
    Property.Number(label="CoolerOffsetOn", configurable=True, description="Offset al encender el compresor"),
    Property.Number(label="CoolerOffsetOff", configurable=True, description="Offset al apagar el compresor"),
    Property.Select(label="AutoStart", options=["Yes", "No"], description="Autostart Fermenter on cbpi start"),
    Property.Select(label="Simulation", options=["No", "Yes"], description="Modo simulación (no activa actores)"),
    Property.Actor(label="MainCompressor", description="Compresor primario"),
    Property.Actor(label="SecondaryCompressor", description="Compresor secundario"),
    Property.Actor(label="ActionActuator", description="Actuador para bomba/válvula"),
    Property.Fermenter(label="DependantFermenter", description="Fermentador dependiente"),
    Property.Number(label="MinTempFermenter", configurable=True),
    Property.Number(label="MaxTempFermenter", configurable=True),
    Property.Number(label="MinTempChillerOffset", configurable=True),
    Property.Number(label="MaxTempChillerOffset", configurable=True),
    Property.Number(label="MaxTempSecondary", configurable=True),
    Property.Number(label="TimeOff", configurable=True),
    Property.Number(label="TimeOn", configurable=True)
])
class GlycolChillerWithDependantTargetTemperature(CBPiFermenterLogic):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compressor2_time = None
        self.actuator_last_on_time = None
        self.actuator_last_off_time = None
        self.compressor2_is_on = False

    def calculate_chiller_target(self, target):
        try:
            slope = (self.max_offset_chiller - self.min_offset_chiller) / (self.max_temp_fermenter - self.min_temp_fermenter)
            target_val = slope * (target - self.min_temp_fermenter) + self.min_offset_chiller
            result = max(self.min_offset_chiller, min(self.max_offset_chiller, target_val))
            logger.info(f"[CHILLER] Temp. objetivo calculada: {result:.2f}")
            return result
        except Exception as e:
            logger.exception("[CHILLER] Error en el cálculo de objetivo de temperatura")
            return self.min_offset_chiller

    async def control_compressor(self, compressor, chiller_temp, target_temp, secondary=False):
        try:
            if secondary:
                logger.info("[COMP2] Comprobando estado secundario...")
                now = datetime.now()
                if -10 <= target_temp <= 20:
                    if chiller_temp >= target_temp:
                        if not self.compressor2_is_on and (self.compressor2_time is None or now - self.compressor2_time >= timedelta(minutes=25)):
                            await self.safe_actor_on(compressor)
                            self.compressor2_time = now
                            self.compressor2_is_on = True
                            logger.info("[COMP2] ON")
                    elif self.compressor2_is_on and now - self.compressor2_time >= timedelta(minutes=180):
                        await self.safe_actor_off(compressor)
                        self.compressor2_time = now
                        self.compressor2_is_on = False
                        logger.info("[COMP2] OFF por timeout")
                    elif self.compressor2_is_on and chiller_temp < target_temp:
                        await self.safe_actor_off(compressor)
                        self.compressor2_time = now
                        self.compressor2_is_on = False
                        logger.info("[COMP2] OFF por temp")
            else:
                if chiller_temp > target_temp:
                    await self.safe_actor_on(compressor)
                else:
                    await self.safe_actor_off(compressor)
        except Exception:
            logger.exception("[COMPRESSOR] Error en el control del compresor")

    async def control_actuator(self, chiller_temp, target_temp):
        try:
            diff = chiller_temp - target_temp
            on_time = timedelta(minutes=0.1) * (10 - min(diff, 10)) / 10 if diff > 0 else timedelta(minutes=0.1)
            off_time = timedelta(minutes=0.5) * (1 + min(diff, 10)) / 10 if diff > 0 else timedelta(minutes=0.5)

            now = datetime.now()

            if self.actuator_last_on_time is None or now - self.actuator_last_on_time >= on_time:
                await self.safe_actor_on(self.action_actuator)
                self.actuator_last_on_time = now

            if self.actuator_last_off_time is None or now - self.actuator_last_off_time >= off_time:
                await self.safe_actor_off(self.action_actuator)
                self.actuator_last_off_time = now
        except Exception:
            logger.exception("[ACTUATOR] Error en el control del actuador")

    async def safe_actor_on(self, actor):
        if self.simulation != "Yes":
            await self.actor_on(actor)
        logger.info(f"[SIM] Activando: {actor} (simulado={self.simulation})")

    async def safe_actor_off(self, actor):
        if self.simulation != "Yes":
            await self.actor_off(actor)
        logger.info(f"[SIM] Desactivando: {actor} (simulado={self.simulation})")

    async def run(self):
        try:
            self.cooler_offset_min = float(self.props.get("CoolerOffsetOn", 0))
            self.cooler_offset_max = float(self.props.get("CoolerOffsetOff", 0))
            self.simulation = self.props.get("Simulation", "No")

            self.compressor1 = self.props.get("MainCompressor")
            self.compressor2 = self.props.get("SecondaryCompressor")
            self.action_actuator = self.props.get("ActionActuator")
            self.fermenter = self.props.get("DependantFermenter")
            self.chiller = self.get_fermenter(self.id)

            self.min_temp_fermenter = float(self.props.get("MinTempFermenter", 0))
            self.max_temp_fermenter = float(self.props.get("MaxTempFermenter", 20))
            self.min_offset_chiller = float(self.props.get("MinTempChillerOffset", -6))
            self.max_offset_chiller = float(self.props.get("MaxTempChillerOffset", 10))
            self.max_secondary_temp = float(self.props.get("MaxTempSecondary", 10))

            while self.running:
                try:
                    chiller_temp = float(self.get_sensor_value(self.chiller.sensor).get("value"))
                    fermenter_target_temp = float(self.get_fermenter_target_temp(self.fermenter))
                    chiller_target = self.calculate_chiller_target(fermenter_target_temp)

                    await self.set_fermenter_target_temp(self.id, round(chiller_target, 2))

                    await self.control_compressor(self.compressor1, chiller_temp, chiller_target, secondary=False)
                    await self.control_compressor(self.compressor2, chiller_temp, chiller_target, secondary=True)

                    await self.control_actuator(chiller_temp, chiller_target)
                except Exception:
                    logger.exception("[MAIN LOOP] Error en ejecución del ciclo principal")

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("[PLUGIN] Tarea cancelada")
        except Exception:
            logger.exception("[PLUGIN] Error inesperado en run")
        finally:
            self.running = False


def setup(cbpi):
    cbpi.plugin.register("GlycolChillerWithDependantTargetTemperature", GlycolChillerWithDependantTargetTemperature)
