import os
import json
import time
import asyncio
from utils.logger import Logger
from scalecodec.base import ScaleBytes
from substrateinterface import SubstrateInterface
from websocket._exceptions import WebSocketBadStatusException
from substrateinterface.exceptions import SubstrateRequestException, ConfigurationError


class SubstrateAPI:
    def __init__(self, config):
        self.config = config
        self.logger = Logger()
        self.logger.info("Initializing SubstrateAPI")

    async def _connect(self, wss):
        max_retries = 3
        wait_seconds = 10

        for attempt in range(1, max_retries + 1):
            try:
                await asyncio.sleep(0.5)
                return SubstrateInterface(
                    url=wss,
                    type_registry_preset=self.config.NETWORK_NAME
                )

            except WebSocketBadStatusException as ws_error:
                self.logger.exception(f"WebSocket error occurred while making a request to Substrate: {ws_error.args}")

                if attempt < max_retries:  # If the current attempt is less than max_retries.
                    self.logger.info(f"Retrying in {wait_seconds} seconds... (Attempt {attempt}/{max_retries})")
                    await asyncio.sleep(wait_seconds)
                    raise
                else:  # If we reached max_retries and couldn't establish a connection.
                    self.logger.error("Max retries reached. Could not establish a connection.")
                    raise

            except SubstrateRequestException as req_error:
                self.logger.exception(f"An error occurred while making a request to Substrate: {req_error.args}")
                raise

            except ConfigurationError as config_error:
                self.logger.exception(f"Config error: {config_error.args}")
                raise

            except Exception as error:
                self.logger.exception(f"An error occurred while initializing the Substrate connection: {error.args}")
                raise

    @staticmethod
    def cache_older_than_24hrs(file_path):
        """Check if a file is older than 24 hours."""
        try:
            # Get the time the file was last modified
            file_modification_time = os.path.getmtime(file_path)
            current_time = time.time()
            # Compare the file's modification time to the current time
            return current_time - file_modification_time > 24 * 3600
        except FileNotFoundError:
            return True

    async def ongoing_referendums_idx(self):
        substrate = None
        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)
            ongoing_referendas = [int(index.value) for index, info in substrate.query_map(module='Referenda', storage_function='ReferendumInfoFor', params=[]) if 'Ongoing' in info]
            return ongoing_referendas
        finally:
            if substrate:
                substrate.close()

    async def referendumInfoFor(self, index=None):
        """
        Get information regarding a specific referendum or all ongoing referendums.

        :param index: (optional) index of the specific referendum
        :return: dictionary containing the information of the specific referendum or a dictionary of all ongoing referendums
        :raises: ValueError if `index` is not None and not a valid index of any referendum
        """
        referendum = {}
        substrate = None
        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)
            if index is not None:
                return substrate.query(
                    module='Referenda',
                    storage_function='ReferendumInfoFor',
                    params=[index]).serialize()
            else:
                qmap = substrate.query_map(
                    module='Referenda',
                    storage_function='ReferendumInfoFor',
                    params=[])
                for index, info in qmap:
                    if 'Ongoing' in info:
                        referendum.update({int(index.value): info.value})

                sort = json.dumps(referendum, sort_keys=True)
                data = json.loads(sort)
                return data

        finally:
            if substrate:
                substrate.close()

    async def check_ss58_address(self, address) -> bool:
        substrate = None
        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)
            if not isinstance(address, str):
                return False
            try:
                if substrate.is_valid_ss58_address(value=address):
                    return True
                else:
                    return False
            except (SubstrateRequestException, ValueError):
                return False

        finally:
            if substrate:
                substrate.close()

    async def referendum_call_data(self, index: int, gov1: bool, call_data: bool):
        """
        Retrieves and decodes the referendum call data based on given parameters.

        Args:
            index (int): The index of the referendum to query.
            gov1 (bool): Determines which module to query ('Democracy' if True, 'Referenda' if False).
            call_data (bool): Determines the type of data to return (raw call data if True, decoded call data if False).

        Returns:
            tuple: A tuple containing a boolean indicating success or failure, and the decoded call data or error message.

        Raises:
            Exception: If an error occurs during the retrieval or decoding process.
        """
        substrate = None

        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)
            referendum = substrate.query(module="Democracy" if gov1 else "Referenda",
                                              storage_function="ReferendumInfoOf" if gov1 else "ReferendumInfoFor",
                                              params=[index]).serialize()

            if referendum is None or 'Ongoing' not in referendum:
                return False, f":warning: Referendum **#{index}** is inactive"

            preimage = referendum['Ongoing']['proposal']

            if 'Inline' in preimage:
                call = preimage['Inline']
                if not call_data:
                    call_obj = substrate.create_scale_object('Call')
                    decoded_call = call_obj.decode(ScaleBytes(call))
                    return decoded_call, preimage
                else:
                    return call

            if 'Lookup' in preimage:
                preimage_hash = preimage['Lookup']['hash']
                preimage_length = preimage['Lookup']['len']
                call = substrate.query(module='Preimage', storage_function='PreimageFor', params=[(preimage_hash, preimage_length)]).value

                if call is None:
                    return False, ":warning: Preimage not found on chain"

                if not call.isprintable():
                    call = f"0x{''.join(f'{ord(c):02x}' for c in call)}"

                if not call_data:
                    call_obj = substrate.create_scale_object('Call')
                    decoded_call = call_obj.decode(ScaleBytes(call))
                    return decoded_call, preimage_hash
                else:
                    return call
        except Exception as ref_caller_error:
            raise ref_caller_error

        finally:
            if substrate:
                substrate.close()

    """
    Cache Super_of
    """

    async def cache_super_of(self, network):
        """
        :param network::
        :return: The super-identity of an alternative 'sub' identity together with its name, within that
        """
        substrate = None

        try:
            if not self.config.PEOPLE_WSS:
                substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)

            if self.config.PEOPLE_WSS:
                substrate = await self._connect(wss=self.config.PEOPLE_WSS)

            result_tmp = {}
            result = substrate.query_map(
                module='Identity',
                storage_function='SuperOf',
                params=[])
            substrate.close()

            for key, values in result:
                result_tmp.update({key.value: values.value})

            with open(f'../data/off-chain-querying/{network}-superof.json', 'w') as superof:
                json.dump(result_tmp, indent=4, fp=superof)

        except Exception as error:
            self.logger.error(f"An error occurred whilst executing cache_identities: {error}")
        finally:
            if substrate:
                substrate.close()

    @staticmethod
    async def check_cached_super_of(address, network):
        with open(f'../data/off-chain-querying/{network}-superof.json', 'r') as superof:
            data = json.load(superof)
            return data.get(address, None)

    async def check_super_of(self, address, network):
        """
        :param address:
        :param network:
        :return: The super-identity of an alternative 'sub' identity together with its name, within that
        """

        if self.cache_older_than_24hrs(f'../data/off-chain-querying/{network}-superof.json'):
            await self.cache_super_of(network=network)

        result = await self.check_cached_super_of(address=address, network=network)

        if result is not None:
            return result[0]
        else:
            return 0

    """
    Cache identityOf
    """

    async def cache_identities(self, network):
        """
        Fetches identities from the 'Identity' module using the 'IdentityOf' storage function,
        and stores the results in a JSON file.

        This function queries the 'Identity' module for identities, and then iterates over the results,
        storing each identity in a temporary dictionary. The dictionary keys are the identity keys,
        and the values are the corresponding identity values.

        After all identities have been stored in the dictionary, the function writes the dictionary
        to a JSON file named 'identity.json'. The JSON file is formatted with an indentation of 4 spaces.

        Raises:
            IOError: If the function cannot write to 'identity.json'.
            JSONDecodeError: If the function cannot serialize the dictionary to JSON.
        """
        substrate = None

        try:
            if not self.config.PEOPLE_WSS:
                substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)

            if self.config.PEOPLE_WSS:
                substrate = await self._connect(wss=self.config.PEOPLE_WSS)

            result_tmp = {}
            result = substrate.query_map(
                module='Identity',
                storage_function='IdentityOf',
                params=[]
            )
            substrate.close()

            for key, values in result:
                result_tmp.update({key.value: values.value})

            with open(f'../data/off-chain-querying/{network}-identity.json', 'w') as identityof:
                json.dump(result_tmp, indent=4, fp=identityof)

        except Exception as error:
            self.logger.error(f"An error occurred whilst executing cache_identities: {error}")
        finally:
            if substrate:
                substrate.close()

    @staticmethod
    async def check_cached_identity(address, network):
        with open(f'../data/off-chain-querying/{network}-identity.json', 'r') as identityof:
            data = json.load(identityof)
            return data.get(address, None)

    async def check_identity(self, address: str, network: str) -> str:
        """
        :param address:
        :param network:
        :return: Information that is pertinent to identify the entity behind an account.
        """
        if self.cache_older_than_24hrs(f'../data/off-chain-querying/{network}-identity.json'):
            await self.cache_identities(network=network)

        result = await self.check_cached_identity(address=address, network=network)
        if result is None:
            super_of = await self.check_super_of(address=address, network=network)

            if super_of:
                result = await self.check_cached_identity(address=super_of, network=network)
            else:
                return address

        display, twitter = None, None

        if isinstance(result, dict):
            display = result['info']['display']
            twitter = result['info']['twitter']
        elif isinstance(result, list):
            display = result[0]['info']['display']
            twitter = result[0]['info']['twitter']

        display_name = display.get('Raw', '')  # Get the 'Raw' value from display, default to empty string if not present
        twitter_name = twitter.get('Raw', '')  # Get the 'Raw' value from twitter, default to empty string if not present

        if display_name and twitter_name:
            return f"{display_name} / {twitter_name}"
        elif display_name:
            return display_name
        elif twitter_name:
            return twitter_name
        else:
            return address

    async def get_average_block_time(self, num_blocks=255):
        substrate = None
        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)
            latest_block_num = substrate.get_block_number(block_hash=substrate.block_hash)
            first_block_num = latest_block_num - num_blocks

            first_timestamp = substrate.query(
                module='Timestamp',
                storage_function='Now',
                block_hash=substrate.get_block_hash(first_block_num)
            ).value

            last_timestamp = substrate.query(
                module='Timestamp',
                storage_function='Now',
                block_hash=substrate.get_block_hash(latest_block_num)
            ).value

            return (last_timestamp - first_timestamp) / (num_blocks * 1000)
        finally:
            if substrate:
                substrate.close()

    async def time_until_block(self, target_block: int) -> int:
        """
        Calculate the estimated time in minutes until the specified target block is reached on the Kusama network.

        Args:
            target_block (int): The target block number for which the remaining time needs to be calculated.

        Returns:
            int: The estimated time remaining in minutes until the target block is reached. If the target block has
            already been reached, the function will return None.

        Raises:
            Exception: If any error occurs while trying to calculate the time remaining until the target block.
        """
        substrate = None
        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)

            # Get the current block number
            current_block = substrate.get_block_number(block_hash=substrate.block_hash)
            if target_block <= current_block:
                self.logger.info("The target block has already been reached.")
                return False

            # Calculate the difference in blocks
            block_difference = target_block - current_block

            # Get the average block time (6 seconds for Kusama)
            avg_block_time = self.get_average_block_time()

            # Calculate the remaining time in seconds
            remaining_time = block_difference * avg_block_time

            # Convert seconds to minutes
            minutes = remaining_time / 60

            return int(minutes)

        except Exception as error:
            self.logger.error(f"An error occurred while trying to calculate minute remaining until {target_block} is met... {error}")
        finally:
            if substrate:
                substrate.close()

    async def get_block_epoch(self, block_number):
        substrate = None
        try:
            substrate = await self._connect(wss=self.config.SUBSTRATE_WSS)
            blockhash = substrate.get_block_hash(block_id=block_number)
            epoch = substrate.query(
                module='Timestamp',
                storage_function='Now',
                block_hash=blockhash
            )

            return epoch.value
        finally:
            if substrate:
                substrate.close()
