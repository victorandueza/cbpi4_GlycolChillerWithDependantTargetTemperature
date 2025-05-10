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
    Property.Number(label="ChillerOffsetOn", configurable=True, description="Offset al encender el compresor"),
    Property.Number(label="ChillerOffsetOff", configurable=True, description="Offset al apagar el compresor"),
    Property.Select(label="AutoStart", options=["Yes", "No"], description="Autostart Fermenter on cbpi start"),
    Property.Select(label="Simulation", options=["No", "Yes"], description="Modo simulación (no activa actores)"),
    Property.Actor(label="MainCompressor", description="Compresor primario"),
    Property.Actor(label="SecondaryCompressor", description="Compresor secundario"),
    Property.Actor(label="ActionActuator", description="Actuador para bomba/válvula"),
    Property.Fermenter(label="DependantFermenter", description="Fermentador dependiente"),
    Property.Number(label="MinTempFermenter", configurable=True),
    Property.Number(label="MaxTempFermenter", configurable=True),
    Property.Number(label="MinTempChillerRange", configurable=True),
    Property.Number(label="MaxTempChillerRange", configurable=True),
    Property.Number(label="MinTempCompressor1Range", configurable=True),
    Property.Number(label="MaxTempCompressor1Range", configurable=True),
    Property.Number(label="MinTempCompressor2Range", configurable=True),
    Property.Number(label="MaxTempCompressor2Range", configurable=True),
    Property.Number(label="Compressor2TimeOff", configurable=True),
    Property.Number(label="Compressor2TimeOn", configurable=True)
])
class GlycolChillerWithDependantTargetTemperature_v0_0_84(CBPiFermenterLogic):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compressor2_time = None
        self.actuator_last_on_time = None
        self.actuator_last_off_time = None
        self.compressor1_is_on = False
        self.compressor2_is_on = False

    def calculate_chiller_target(self, target):
        try:
            slope = (self.max_range_chiller - self.min_range_chiller) / (self.max_temp_fermenter - self.min_temp_fermenter)
            target_val = slope * (target - self.min_temp_fermenter) + self.min_range_chiller
            result = max(self.min_range_chiller, min(self.max_range_chiller, target_val))
            logger.info(f"[CHILLER] Temp. objetivo calculada: {result:.2f}")
            return result
        except Exception as e:
            logger.exception("[CHILLER] Error en el cálculo de objetivo de temperatura")
            return self.min_range_chiller

    async def control_compressor(self, compressor, current_temp, target_temp, secondary=False):
        try:
            now = datetime.now()

            # Histeresis común para ambos compresores
            offset_on = self.chiller_offset_min
            offset_off = self.chiller_offset_max

            if secondary:
                logger.info("[COMP2] Comprobando estado secundario...")
                if self.compressor2_min_temp <= target_temp <= self.compressor2_max_temp:
                    if not self.compressor2_is_on:
                        if current_temp >= target_temp + offset_on and (
                            self.compressor2_time is None or now - self.compressor2_time >= timedelta(minutes=self.compressor2_time_off)):
                            await self.safe_actor_on(compressor)
                            self.compressor2_time = now
                            self.compressor2_is_on = True
                            logger.info("[COMP2] ON por histéresis")
                    elif self.compressor2_is_on:
                        if now - self.compressor2_time >= timedelta(minutes=self.compressor2_time_on):
                            await self.safe_actor_off(compressor)
                            self.compressor2_time = now
                            self.compressor2_is_on = False
                            logger.info("[COMP2] OFF por timeout")
                        elif current_temp <= target_temp - offset_off:
                            await self.safe_actor_off(compressor)
                            self.compressor2_time = now
                            self.compressor2_is_on = False
                            logger.info("[COMP2] OFF por histéresis")
            else:
                if self.compressor1_min_temp <= target_temp <= self.compressor1_max_temp:                    
                    if not self.compressor1_is_on and current_temp >= target_temp + offset_on:
                        await self.safe_actor_on(compressor)
                        self.compressor1_is_on = True
                        logger.info("[COMP1] ON por histéresis")
                    elif self.compressor1_is_on and current_temp <= target_temp - offset_off:
                        await self.safe_actor_off(compressor)
                        self.compressor1_is_on = False
                        logger.info("[COMP1] OFF por histéresis")
        except Exception:
            logger.exception("[COMPRESSOR] Error en el control del compresor")

    async def control_actuator(self, current_temp, target_temp):
        try:
            diff = current_temp - target_temp
            if diff > 0:
                # Limita diff a un máximo de 10 para simplificar la lógica
                adjusted_diff = min(diff, 10)

                # A mayor diferencia, más corto es el ciclo ON y más largo el OFF.
                on_minutes = 0.1 * (10 - adjusted_diff) / 10
                off_minutes = 0.5 * (1 + adjusted_diff) / 10
            else:
                # Valores por defecto cuando no hay diferencia positiva
                on_minutes = 0.1
                off_minutes = 0.5

            on_time = timedelta(minutes=on_minutes)
            off_time = timedelta(minutes=off_minutes)


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
            self.simulation = self.props.get("Simulation", "No")

            self.compressor1 = self.props.get("MainCompressor")
            self.compressor2 = self.props.get("SecondaryCompressor")
            self.action_actuator = self.props.get("ActionActuator")
            self.fermenter = self.props.get("DependantFermenter")
            self.chiller = self.get_fermenter(self.id)

            self.compressor1_min_temp = float(self.props.get("MinTempCompressor1Range", -10))
            self.compressor1_max_temp = float(self.props.get("MaxTempCompressor1Range", 20))
            self.compressor2_min_temp = float(self.props.get("MinTempCompressor2Range", -10))
            self.compressor2_max_temp = float(self.props.get("MaxTempCompressor2Range", 5))

            self.compressor2_time_off = float(self.props.get("Compressor2TimeOff", 25))
            self.compressor2_time_on = float(self.props.get("Compressor2TimeOn", 180))

            self.min_temp_fermenter = float(self.props.get("MinTempFermenter", 0))
            self.max_temp_fermenter = float(self.props.get("MaxTempFermenter", 20))
            self.min_range_chiller = float(self.props.get("MinTempChillerRange", -6))
            self.max_range_chiller = float(self.props.get("MaxTempChillerRange", 10))
            self.chiller_offset_min = float(self.props.get("ChillerOffsetOn", 1))
            self.chiller_offset_max = float(self.props.get("ChillerOffsetOff", 1))

            while self.running:
                try:
                    chiller_current_temp = float(self.get_sensor_value(self.chiller.sensor).get("value"))
                    fermenter_target_temp = float(self.get_fermenter_target_temp(self.fermenter))
                    chiller_target_temp = self.calculate_chiller_target(fermenter_target_temp)

                    await self.set_fermenter_target_temp(self.id, round(chiller_target, 2))

                    await self.control_compressor(self.compressor1, chiller_current_temp, chiller_target_temp, secondary=False)
                    await self.control_compressor(self.compressor2, chiller_current_temp, chiller_target_temp, secondary=True)

                    await self.control_actuator(chiller_current_temp, chiller_target_temp)
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
    cbpi.plugin.register("ChillerDepTemp_v0_0_84", GlycolChillerWithDependantTargetTemperature_v0_0_84)
