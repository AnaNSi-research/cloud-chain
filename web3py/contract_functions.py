import asyncio
import threading

from eth_typing import Address
from web3 import Web3, AsyncHTTPProvider, HTTPProvider, WebsocketProvider
from web3.eth import AsyncEth
from web3.exceptions import TimeExhausted
from web3.middleware import geth_poa_middleware

from settings import (
    HTTP_URI, WEB_SOCKET_URI,
    COMPILED_FACTORY_PATH, COMPILED_ORACLE_PATH,
    COMPILED_CLOUD_SLA_PATH, DEBUG
)
from utility import get_addresses, get_settings, get_contract, check_statuses


class ContractTest:
    def __init__(self, blockchain):
        self.factory_address, self.oracle_address = get_addresses(blockchain)
        self.accounts, self.private_keys = get_settings(blockchain)

        self.w3_async = Web3(
            AsyncHTTPProvider(HTTP_URI),
            modules={
                'eth': AsyncEth
            },
            middlewares=[]  # geth_poa_middleware not supported yet
        )

        if blockchain == 'polygon':
            self.w3 = Web3(HTTPProvider(HTTP_URI))
        else:
            self.w3 = Web3(WebsocketProvider(WEB_SOCKET_URI))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        self.lock = threading.Lock()
        self.nonces = []
        for idx in range(3):
            self.nonces.append(self.w3.eth.get_transaction_count(self.accounts[idx]))

    async def get_nonce(self, idx: int):
        nonce = await self.w3_async.eth.get_transaction_count(self.accounts[idx])
        return nonce

    async def get_nonce_lock(self, idx: int):
        self.lock.acquire()
        await asyncio.sleep(.1)
        tmp = self.nonces[idx]
        self.nonces[idx] = self.nonces[idx] + 1
        self.lock.release()
        return tmp

    async def update_nonces(self):
        self.lock.acquire()
        for i in range(3):
            self.nonces[i] = await self.w3_async.eth.get_transaction_count(self.accounts[i])
        self.lock.release()

    async def sign_send_transaction(self, tx: dict, pk: str) -> int:
        try:
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=pk)
            tx_hash = await self.w3_async.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_receipt = await self.w3_async.eth.wait_for_transaction_receipt(tx_hash)
        except (ValueError, TimeExhausted) as e:
            if DEBUG:
                print(f"{type(e)} [sign_send]: {e}")
            return 0
        else:
            return tx_receipt['status']

    async def cloud_sla_creation_activation(self) -> tuple:
        statuses = []

        # Parameters
        price = Web3.toWei(0.001, 'ether')  # 5
        test_validity_duration = 60 ** 2

        # Contracts
        contract_factory = get_contract(self.w3, self.factory_address, COMPILED_FACTORY_PATH)
        contract_oracle = get_contract(self.w3, self.oracle_address, COMPILED_ORACLE_PATH)

        # Transactions
        tx_create_child = contract_factory.functions.createChild(
            contract_oracle.address,
            self.accounts[1],
            price,
            test_validity_duration,
            1,
            1
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[0],
            'nonce': await self.get_nonce(0)
        })
        statuses.append(await self.sign_send_transaction(tx_create_child, self.private_keys[0]))

        # tx_sm_address = await self.w3_async.eth.call(contract_factory.functions.getSmartContractAddress(
        # self.accounts[1]))
        tx_sm_address = contract_factory.functions.getSmartContractAddress(
            self.accounts[1]
        ).call()

        # Contract
        contract_cloud_sla = get_contract(self.w3, tx_sm_address, COMPILED_CLOUD_SLA_PATH)

        # Transaction
        tx_deposit = contract_cloud_sla.functions.Deposit().buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1),
            'value': price
        })
        statuses.append(await self.sign_send_transaction(tx_deposit, self.private_keys[1]))

        all_statuses = check_statuses(statuses)

        if all_statuses and DEBUG:
            print('CloudSLA creation and activation: OK')
            print(f'\taddress: {tx_sm_address}')

        return tx_sm_address, all_statuses

    async def sequence_upload(self, cloud_address: Address, filepath: str, hash_digest: str) -> bool:
        statuses = []

        # Contract
        contract_cloud_sla = get_contract(self.w3, cloud_address, COMPILED_CLOUD_SLA_PATH)

        # Transactions
        challenge = Web3.solidityKeccak(
            ['bytes32'], [hash_digest]
        )

        tx_upload_request = contract_cloud_sla.functions.UploadRequest(
            filepath,
            challenge
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1)
        })
        statuses.append(await self.sign_send_transaction(tx_upload_request, self.private_keys[1]))

        tx_upload_request_ack = contract_cloud_sla.functions.UploadRequestAck(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[0],
            'nonce': await self.get_nonce(0)
        })
        statuses.append(await self.sign_send_transaction(tx_upload_request_ack, self.private_keys[0]))

        tx_upload_transfer_ack = contract_cloud_sla.functions.UploadTransferAck(
            filepath,
            hash_digest
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[0],
            'nonce': await self.get_nonce(0)
        })
        statuses.append(await self.sign_send_transaction(tx_upload_transfer_ack, self.private_keys[0]))

        all_statuses = check_statuses(statuses)

        return all_statuses

    async def sequence_read(self, cloud_address: Address, filepath: str, url: str) -> bool:
        statuses = []

        # Contract
        contract_cloud_sla = get_contract(self.w3, cloud_address, COMPILED_CLOUD_SLA_PATH)

        # Transactions
        tx_read_request = contract_cloud_sla.functions.ReadRequest(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1)
        })
        statuses.append(await self.sign_send_transaction(tx_read_request, self.private_keys[1]))

        tx_read_request_ack = contract_cloud_sla.functions.ReadRequestAck(
            filepath,
            url
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[0],
            'nonce': await self.get_nonce(0)
        })
        statuses.append(await self.sign_send_transaction(tx_read_request_ack, self.private_keys[0]))

        all_statuses = check_statuses(statuses)

        return all_statuses

    async def sequence_file(self, cloud_address: Address, filepath: str, url: str, hash_digest: str) -> bool:
        statuses = []

        # Contracts
        contract_cloud_sla = get_contract(self.w3, cloud_address, COMPILED_CLOUD_SLA_PATH)
        contract_oracle = get_contract(self.w3, self.oracle_address, COMPILED_ORACLE_PATH)

        # Transactions
        tx_file_hash_request = contract_cloud_sla.functions.FileHashRequest(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1)
        })
        statuses.append(await self.sign_send_transaction(tx_file_hash_request, self.private_keys[1]))

        tx_digit_store = contract_oracle.functions.DigestStore(
            url,
            hash_digest
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[2],
            'nonce': await self.get_nonce(2)
        })
        statuses.append(await self.sign_send_transaction(tx_digit_store, self.private_keys[2]))

        tx_file_check = contract_cloud_sla.functions.FileCheck(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1)
        })
        statuses.append(await self.sign_send_transaction(tx_file_check, self.private_keys[1]))

        all_statuses = check_statuses(statuses)

        return all_statuses

    async def upload(self, cloud_address: Address) -> bool:
        # Parameters
        filepath = 'test.pdf'
        hash_digest = '0x9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'

        all_statuses = await self.sequence_upload(cloud_address, filepath, hash_digest)

        if all_statuses and DEBUG:
            print('Upload: OK')

        return all_statuses

    async def read(self, cloud_address: Address) -> bool:
        # Parameters
        filepath = 'test.pdf'
        url = 'www.test.com'

        all_statuses = await self.sequence_read(cloud_address, filepath, url)

        if all_statuses and DEBUG:
            print('Read: OK')

        return all_statuses

    async def delete(self, cloud_address: Address) -> bool:
        statuses = []

        # Parameter
        filepath = 'test.pdf'

        # Contract
        contract_cloud_sla = get_contract(self.w3, cloud_address, COMPILED_CLOUD_SLA_PATH)

        # Transactions
        tx_delete_request = contract_cloud_sla.functions.DeleteRequest(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1)
        })
        statuses.append(await self.sign_send_transaction(tx_delete_request, self.private_keys[1]))

        tx_delete = contract_cloud_sla.functions.Delete(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[0],
            'nonce': await self.get_nonce(0)
        })
        statuses.append(await self.sign_send_transaction(tx_delete, self.private_keys[0]))

        all_statuses = check_statuses(statuses)

        if all_statuses and DEBUG:
            print('Delete: OK')

        return all_statuses

    async def file_check_undeleted_file(self, cloud_address: Address) -> bool:
        # Parameters
        filepath = 'test.pdf'
        url = 'www.test.com'
        hash_digest = '0x9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'

        all_statuses = await self.sequence_file(cloud_address, filepath, url, hash_digest)

        if all_statuses and DEBUG:
            print('File check for undeleted file: OK')

        return all_statuses

    async def another_file_upload(self, cloud_address: Address) -> bool:
        # Parameters
        filepath = 'test2.pdf'
        hash_digest = '0x1f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'

        all_statuses = await self.sequence_upload(cloud_address, filepath, hash_digest)

        if all_statuses and DEBUG:
            print('Another file upload: OK')

        return all_statuses

    async def read_deny_lost_file_check(self, cloud_address: Address) -> bool:
        statuses = []

        # Parameter
        filepath = 'test2.pdf'

        # Contract
        contract_cloud_sla = get_contract(self.w3, cloud_address, COMPILED_CLOUD_SLA_PATH)

        # Transactions
        tx_read_request = contract_cloud_sla.functions.ReadRequest(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[1],
            'nonce': await self.get_nonce(1)
        })
        statuses.append(await self.sign_send_transaction(tx_read_request, self.private_keys[1]))

        tx_read_request_deny = contract_cloud_sla.functions.ReadRequestDeny(
            filepath
        ).buildTransaction({
            'gasPrice': 0,
            'from': self.accounts[0],
            'nonce': await self.get_nonce(0)
        })
        statuses.append(await self.sign_send_transaction(tx_read_request_deny, self.private_keys[0]))

        all_statuses = check_statuses(statuses)

        if all_statuses and DEBUG:
            print('Read Deny with lost file check: OK')

        return all_statuses

    async def another_file_upload_read(self, cloud_address: Address) -> bool:
        # Parameters
        filepath = 'test3.pdf'
        url = 'www.test3.com'
        hash_digest = '0x2f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'

        all_statuses_upload = await self.sequence_upload(cloud_address, filepath, hash_digest)
        all_statuses_read = await self.sequence_read(cloud_address, filepath, url)

        if all_statuses_upload and all_statuses_read and DEBUG:
            print('Another file upload + read: OK')

        return all_statuses_upload and all_statuses_read

    async def corrupted_file_check(self, cloud_address: Address) -> bool:
        # Parameters
        filepath = 'test3.pdf'
        url = 'www.test3.com'
        hash_digest = '0x4f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'

        all_statuses = await self.sequence_file(cloud_address, filepath, url, hash_digest)

        if all_statuses and DEBUG:
            print('File Check for corrupted file: OK')

        return all_statuses
