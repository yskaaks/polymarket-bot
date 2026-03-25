"""Synthetic data fixtures for testing data loaders."""
import tempfile
import os


def create_becker_fixture_dir() -> str:
    """Create a temporary directory mimicking the Becker repo data layout."""
    import duckdb

    tmpdir = tempfile.mkdtemp(prefix="becker_test_")
    os.makedirs(f"{tmpdir}/polymarket/markets", exist_ok=True)
    os.makedirs(f"{tmpdir}/polymarket/trades", exist_ok=True)
    os.makedirs(f"{tmpdir}/polymarket/blocks", exist_ok=True)

    con = duckdb.connect()

    con.execute(f"""
        COPY (
            SELECT
                'cond_001' as condition_id,
                'Will BTC hit 100k?' as question,
                'btc-100k' as slug,
                '["Yes","No"]' as outcomes,
                '[0.65, 0.35]' as outcome_prices,
                '["tok_yes_001","tok_no_001"]' as clob_token_ids,
                500000.0 as volume,
                1 as active,
                0 as closed,
                '2024-12-31T00:00:00Z' as end_date,
                '2024-01-01T00:00:00Z' as created_at
            UNION ALL
            SELECT
                'cond_002', 'Will ETH hit 5k?', 'eth-5k',
                '["Yes","No"]', '[0.30, 0.70]',
                '["tok_yes_002","tok_no_002"]',
                100000.0, 0, 1,
                '2024-06-30T00:00:00Z', '2024-01-15T00:00:00Z'
        ) TO '{tmpdir}/polymarket/markets/markets_0_1.parquet' (FORMAT PARQUET)
    """)

    # Schema must match real Becker trades: maker_asset_id/taker_asset_id/maker_amount/taker_amount
    # maker_asset_id='0' means maker is buying with USDC -> taker_asset_id is the token
    # Amounts use 6 decimal scaling (1_000_000 = 1.0)
    con.execute(f"""
        COPY (
            SELECT
                50000000 as block_number, 'tx_aaa' as transaction_hash, 0 as log_index,
                'order_aaa' as order_hash,
                '0xmaker1' as maker, '0xtaker1' as taker,
                '0' as maker_asset_id, 'tok_yes_001' as taker_asset_id,
                650000 as maker_amount, 1000000 as taker_amount,
                0 as fee
            UNION ALL
            SELECT 50000100, 'tx_bbb', 0, 'order_bbb',
                '0xmaker2', '0xtaker2',
                'tok_yes_001', '0',
                500000, 340000,
                0
            UNION ALL
            SELECT 50000200, 'tx_ccc', 0, 'order_ccc',
                '0xmaker3', '0xtaker3',
                '0', 'tok_no_001',
                350000, 1000000,
                0
        ) TO '{tmpdir}/polymarket/trades/trades_0_1.parquet' (FORMAT PARQUET)
    """)

    con.execute(f"""
        COPY (
            SELECT 50000000 as block_number, 1718448000 as timestamp
            UNION ALL SELECT 50000100, 1718448200
            UNION ALL SELECT 50000200, 1718448400
        ) TO '{tmpdir}/polymarket/blocks/blocks_0_1.parquet' (FORMAT PARQUET)
    """)

    con.close()
    return tmpdir
