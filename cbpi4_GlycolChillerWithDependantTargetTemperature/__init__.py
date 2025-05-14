import asyncio
import logging
from cbpi.api import *
from datetime import datetime, timedelta
import pkg_resources

LOG_ACTIVO = False  # Cambia a True para habilitar logs

logger = logging.getLogger(__name__)

if LOG_ACTIVO:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.CRITICAL + 1)  # No emite ningún log

if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)  # No importa, porque logger filtra antes
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


try:
    version = pkg_resources.get_distribution("cbpi4_GlycolChillerWithDependantTargetTemperature").version
    logger.info(f"[PLUGIN] GlycolChiller plugin cargado – versión {version}")
except Exception as e:
    logger.warning(f"[PLUGIN] No se pudo obtener la versión del plugin: {e}")

@parameters([
    Property.Number(label="ChillerOffsetOn", configurable=True, description="Offset al encender el compresor"),
    Property.Number(label="ChillerOffsetOff", configurable=True, description="Offset al apagar el compresor"),
    Property.Select(label="AutoStart", options=["Yes", "No"], description="Autostart Fermenter on cbpi start"),
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
class GlycolChillerWithDependantTargetTemperature_v0_0_142(CBPiFermenterLogic):

    def __init__(self, cbpi, id, props):
        super().__init__(cbpi, id, props)
        self.api = cbpi
        self.compressor2_has_been_on = False
        self.compressor2_time = None
        self.actuator_last_on_time = None
        self.actuator_last_off_time = None
        self.compressor1_is_on = False
        self.compressor2_is_on = False
        self.actuator_state = "off"

    def calculate_chiller_target(self, target):
        try:
            slope = (self.max_range_chiller - self.min_range_chiller) / (self.max_temp_fermenter - self.min_temp_fermenter)
            target_val = slope * (target - self.min_temp_fermenter) + self.min_range_chiller
            result = max(self.min_range_chiller, min(self.max_range_chiller, target_val))
            logger.debug(f"[CHILLER] Temp. objetivo calculada: {result:.2f}")
            return result
        except Exception as e:
            logger.exception("[CHILLER] Error en el cálculo de objetivo de temperatura")
            return self.min_range_chiller

    async def control_compressor(self, compressor, current_temp, target_temp, secondary=False):
        try:
            now = datetime.now()
            offset_on = self.chiller_offset_min
            offset_off = self.chiller_offset_max

            logger.debug(f"[CHILLER] {'[COMPRESSOR2]' if secondary else '[COMPRESSOR1]'} – Temp actual: {current_temp:.2f}°C | Target: {target_temp:.2f}°C | ON offset: {offset_on} | OFF offset: {offset_off}")

            if secondary:
                await self._control_compressor2(compressor, current_temp, target_temp, now, offset_on, offset_off)
            else:
                await self._control_compressor1(compressor, current_temp, target_temp, now, offset_on, offset_off)

        except Exception:
            logger.exception("[CHILLER] [COMPRESSOR] Error en el control del compresor")

    async def _control_compressor1(self, compressor, current_temp, target_temp, now, offset_on, offset_off):
        if target_temp > self.compressor1_max_temp:
            await self.safe_actor_off(compressor)
            self.compressor1_is_on = False
            logger.info("[CHILLER] [COMPRESSOR1] APAGADO por rango")
            return

        if not self.compressor1_is_on:
            logger.debug("[CHILLER] [COMPRESSOR1] Está apagado. Evaluando condiciones para encender.")
            if current_temp >= target_temp + offset_on:
                await self.safe_actor_on(compressor)
                self.compressor1_is_on = True
                logger.info("[CHILLER] [COMPRESSOR1] ENCENDIDO por histéresis")
        else:
            logger.debug("[CHILLER] [COMPRESSOR1] Está encendido. Evaluando condiciones para apagar.")
            if current_temp <= target_temp - offset_off:
                await self.safe_actor_off(compressor)
                self.compressor1_is_on = False
                logger.info("[CHILLER] [COMPRESSOR1] APAGADO por histéresis")
        
    async def _control_compressor2(self, compressor, current_temp, target_temp, now, offset_on, offset_off):
        logger.debug("[CHILLER] [COMPRESSOR2] Comprobando condiciones para compresor secundario")

        if not (self.compressor2_min_temp <= target_temp <= self.compressor2_max_temp):
            await self._turn_off_comp2("rango", compressor, now)
            return

        if not self.compressor2_is_on:
            await self._try_turn_on_comp2(current_temp, target_temp, offset_on, now, compressor)
        else:
            await self._try_turn_off_comp2(current_temp, target_temp, offset_off, now, compressor)

    async def _try_turn_on_comp2(self, current_temp, target_temp, offset_on, now, compressor):
        logger.debug("[CHILLER] [COMPRESSOR2] Está apagado. Evaluando condiciones para encender.")
        
        tiempo_apagado = float('inf') if self.compressor2_time is None else (now - self.compressor2_time).total_seconds() / 60
        tiempo_maximo = self.compressor2_time_off
        tiempo_restante = max(0, tiempo_maximo - tiempo_apagado)

        logger.debug(
            f"----[CHILLER] [COMPRESSOR2] Tiempo apagado: {tiempo_apagado:.1f} min | "
            f"Tiempo restante: {tiempo_restante:.1f} min"
        )

        can_start = (
            current_temp >= target_temp + offset_on and (
                not self.compressor2_has_been_on or
                tiempo_apagado >= tiempo_maximo
            )
        )

        if can_start:
            await self._turn_on_comp2(compressor, now)

    async def _try_turn_off_comp2(self, current_temp, target_temp, offset_off, now, compressor):
        logger.debug("[CHILLER] [COMPRESSOR2] Está encendido. Evaluando condiciones para apagar.")

        tiempo_encendido = float('inf') if self.compressor2_time is None else (now - self.compressor2_time).total_seconds() / 60
        tiempo_maximo = self.compressor2_time_on
        tiempo_restante = max(0, tiempo_maximo - tiempo_encendido)

        logger.debug(
            f"----[CHILLER] [COMPRESSOR2] Tiempo encendido: {tiempo_encendido:.1f} min | "
            f"Tiempo restante: {tiempo_restante:.1f} min"
        )

        if tiempo_encendido >= tiempo_maximo:
            await self._turn_off_comp2("tiempo máximo", compressor, now)
        elif current_temp <= target_temp - offset_off:
            await self._turn_off_comp2("histéresis", compressor, now)

    
    async def _turn_on_comp2(self, compressor, now):
        await self.safe_actor_on(compressor)
        self.compressor2_time = now
        self.compressor2_is_on = True
        self.compressor2_has_been_on = True
        logger.info("[CHILLER] [COMP2] ENCENDIDO por histéresis")

    async def _turn_off_comp2(self, reason, compressor, now):
        await self.safe_actor_off(compressor)
        # Solo actualizamos el tiempo si estaba realmente encendido
        if self.compressor2_is_on:
            self.compressor2_time = now
        self.compressor2_is_on = False
        logger.info(f"[CHILLER] [COMP2] APAGADO por {reason}")


    async def control_actuator(self, current_temp, target_temp):
        try:
            try:
                with open("/home/cbpi/fermenter_action_required.txt", "r") as f:
                    value = f.read().strip()
                    action_required = (value == "1")
                    logger.debug(f"[CHILLER] [ACTUATOR] Action Required leido: {action_required}")
            except FileNotFoundError:
                logger.warning("[CHILLER] [ACTUATOR] Archivo de estado no encontrado.")
            except Exception:
                logger.exception("[CHILLER] [ACTUATOR] Error al leer el archivo de estado")

            #----------------- Control temporizado del actuador ------------------
            if action_required:
                diff = current_temp - target_temp
                adjusted_diff = min(max(diff, 0), 10)  # limita entre 0 y 10

                total_cycle_seconds = 120
                min_time = 5  # mínimo 5 segundos

                # Calcular tiempos proporcionales crudos
                raw_on = total_cycle_seconds * (1 - adjusted_diff / 10)
                raw_off = total_cycle_seconds * (adjusted_diff / 10)

                # Aplicar mínimos
                on_seconds = max(raw_on, min_time)
                off_seconds = max(raw_off, min_time)

                # Ajustar si al aplicar mínimos se supera el total del ciclo
                if on_seconds + off_seconds > total_cycle_seconds:
                    overflow = (on_seconds + off_seconds) - total_cycle_seconds
                    # Restar proporcionalmente el exceso
                    if on_seconds > off_seconds:
                        on_seconds -= overflow
                    else:
                        off_seconds -= overflow

                # Convertir a timedelta
                on_time = timedelta(seconds=on_seconds)
                off_time = timedelta(seconds=off_seconds)

                logger.debug(f"[CHILLER] [ACTUATOR] Diff: {diff:.2f} | ON: {on_seconds:.1f} s | OFF: {off_seconds:.1f} s")

                now = datetime.now()
                self.actuator_last_switch_time = now

                elapsed = now - self.actuator_last_switch_time

                if self.actuator_state == "off" and elapsed >= off_time:
                    await self.safe_actor_on(self.action_actuator)
                    self.actuator_state = "on"
                    self.actuator_last_switch_time = now
                    logger.info("[CHILLER] [ACTUATOR] ENCENDIDO")

                elif self.actuator_state == "on" and elapsed >= on_time:
                    await self.safe_actor_off(self.action_actuator)
                    self.actuator_state = "off"
                    self.actuator_last_switch_time = now
                    logger.info("[CHILLER] [ACTUATOR] APAGADO")


        except Exception as e:
            logger.exception(f"[CHILLER] [ACTUATOR] Error en el control del actuador: {e}")


    async def safe_actor_on(self, actor):
        await self.actor_on(actor)

    async def safe_actor_off(self, actor):
        await self.actor_off(actor)

    async def run(self):
        try:
            logger.debug("[CHILLER] Iniciando ejecución del plugin")

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

                    logger.debug(f"[CHILLER] Temp actual del chiller: {chiller_current_temp:.2f}°C | Temp objetivo para el chiller: {chiller_target_temp:.2f}°C")
                    logger.debug(f"[CHILLER] Temp objetivo fermentador: {fermenter_target_temp:.2f}°C")

                    await self.set_fermenter_target_temp(self.id, round(chiller_target_temp, 2))

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
            logger.info("[CHILLER] Deteniendo plugin, apagando actuadores...")

            try:
                if hasattr(self, "compressor1") and self.compressor1 is not None:
                    await self.safe_actor_off(self.compressor1)
                    logger.info("[CHILLER] Compresor 1 apagado al finalizar")

                if hasattr(self, "compressor2") and self.compressor2 is not None:
                    await self.safe_actor_off(self.compressor2)
                    logger.info("[CHILLER] Compresor 2 apagado al finalizar")

                if hasattr(self, "action_actuator") and self.action_actuator is not None:
                    await self.safe_actor_off(self.action_actuator)
                    logger.info("[CHILLER] Actuador auxiliar apagado al finalizar")

            except Exception:
                logger.exception("[CHILLER] Error al apagar los actuadores al detener el plugin")


def setup(cbpi):
    cbpi.plugin.register("ChillerDepTemp_v0_0_142", GlycolChillerWithDependantTargetTemperature_v0_0_142)

