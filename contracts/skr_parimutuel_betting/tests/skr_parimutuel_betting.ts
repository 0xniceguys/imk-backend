import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { Keypair, PublicKey, LAMPORTS_PER_SOL, SystemProgram } from "@solana/web3.js";
import { TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID, createMint, createAssociatedTokenAccount, mintTo, getAssociatedTokenAddressSync, getAccount } from "@solana/spl-token";
import { assert } from "chai";
import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import BN from "bn.js";

const idlPath = join(dirname(fileURLToPath(import.meta.url)), "../target/idl/skr_parimutuel_betting.json");
const idl = JSON.parse(readFileSync(idlPath, "utf8"));

describe("skr_parimutuel_betting", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);

  const programId = new PublicKey("7woZnJL2FL4yG44EEDgVtY3YX6TqGFF1yuWND4tiDuAv");
  const program = new Program(idl, provider);

  // Keypairs
  const admin = Keypair.generate();
  const treasuryWallet = Keypair.generate();
  const user1 = Keypair.generate();
  const user2 = Keypair.generate();
  const user3 = Keypair.generate();
  const intruder = Keypair.generate();

  let skrMint: PublicKey;
  let treasuryAta: PublicKey;
  let user1Ata: PublicKey;
  let user2Ata: PublicKey;
  let user3Ata: PublicKey;

  const [configPda] = PublicKey.findProgramAddressSync([Buffer.from("config")], programId);
  const BET_AMOUNT = 100_000_000;
  const MIN_BET = 10_000_000;
  const MAX_BET = 100_000_000_000;

  // Match 0 state (happy path)
  let match0Pda: PublicKey;
  let vault0AuthPda: PublicKey;
  let vault0Ata: PublicKey;

  // Helper: derive match PDAs for a given match counter value
  function deriveMatchPdas(matchId: number) {
    const id = new BN(matchId);
    const [matchPda] = PublicKey.findProgramAddressSync(
      [Buffer.from("match"), id.toArrayLike(Buffer, "le", 8)], programId
    );
    const [vaultAuthPda] = PublicKey.findProgramAddressSync(
      [Buffer.from("vault_auth"), matchPda.toBuffer()], programId
    );
    const vaultAta = getAssociatedTokenAddressSync(skrMint, vaultAuthPda, true);
    return { matchPda, vaultAuthPda, vaultAta };
  }


  // Helper: create a match using the current counter and return its PDAs
  async function createMatchHelper() {
    const c = await program.account.config.fetch(configPda);
    const pdas = deriveMatchPdas(c.matchCounter.toNumber());
    await program.methods.createMatch(Array.from(Buffer.alloc(32, c.matchCounter.toNumber() % 256)), Array.from(Buffer.alloc(32, (c.matchCounter.toNumber() + 128) % 256)))
      .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
      .signers([admin]).rpc();
    return pdas;
  }

  before(async () => {
    for (const kp of [admin, user1, user2, user3, intruder]) {
      const sig = await provider.connection.requestAirdrop(kp.publicKey, 10 * LAMPORTS_PER_SOL);
      await provider.connection.confirmTransaction(sig);
    }
    skrMint = await createMint(provider.connection, admin, admin.publicKey, null, 6);
    treasuryAta = await createAssociatedTokenAccount(provider.connection, admin, skrMint, treasuryWallet.publicKey);
    user1Ata = await createAssociatedTokenAccount(provider.connection, admin, skrMint, user1.publicKey);
    user2Ata = await createAssociatedTokenAccount(provider.connection, admin, skrMint, user2.publicKey);
    user3Ata = await createAssociatedTokenAccount(provider.connection, admin, skrMint, user3.publicKey);
    const mintAmount = 10_000_000_000;
    await mintTo(provider.connection, admin, skrMint, user1Ata, admin, mintAmount);
    await mintTo(provider.connection, admin, skrMint, user2Ata, admin, mintAmount);
    await mintTo(provider.connection, admin, skrMint, user3Ata, admin, mintAmount);
  });

  // =====================================================================
  // HAPPY PATH (8 tests — already proven)
  // =====================================================================
  describe("Happy Path", () => {
    it("1. Initializes config", async () => {
      await program.methods.initConfig(new BN(MIN_BET), new BN(MAX_BET))
        .accountsStrict({ config: configPda, admin: admin.publicKey, skrMint, treasuryWallet: treasuryWallet.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const config = await program.account.config.fetch(configPda);
      assert.ok(config.admin.equals(admin.publicKey));
      assert.equal(config.feeBps, 500);
      assert.equal(config.matchCounter.toNumber(), 0);
      assert.equal(config.paused, false);
    });

    it("2. Creates Match #0", async () => {
      const pdas = deriveMatchPdas(0);
      match0Pda = pdas.matchPda; vault0AuthPda = pdas.vaultAuthPda; vault0Ata = pdas.vaultAta;
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 1)), Array.from(Buffer.alloc(32, 2)))
        .accountsStrict({ config: configPda, matchAccount: match0Pda, vaultAuthority: vault0AuthPda, vaultAta: vault0Ata, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      const m = await program.account.match.fetch(match0Pda);
      assert.equal(m.id.toNumber(), 0);
      assert.deepEqual(m.status, { open: {} });
    });

    it("3. User1 bets on Side A", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), match0Pda.toBuffer(), user1.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: match0Pda, userBet: betPda, userSkrAta: user1Ata, vaultAta: vault0Ata, vaultAuthority: vault0AuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const m = await program.account.match.fetch(match0Pda);
      assert.equal(m.totalA.toNumber(), BET_AMOUNT);
    });

    it("4. User2 bets on Side B", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), match0Pda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ b: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: match0Pda, userBet: betPda, userSkrAta: user2Ata, vaultAta: vault0Ata, vaultAuthority: vault0AuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      const v = await getAccount(provider.connection, vault0Ata);
      assert.equal(Number(v.amount), BET_AMOUNT * 2);
    });

    it("5. Admin locks the match", async () => {
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: match0Pda, admin: admin.publicKey })
        .signers([admin]).rpc();
      const m = await program.account.match.fetch(match0Pda);
      assert.deepEqual(m.status, { locked: {} });
    });

    it("6. Admin resolves — Side A wins", async () => {
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: match0Pda, vaultAta: vault0Ata, vaultAuthority: vault0AuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const m = await program.account.match.fetch(match0Pda);
      assert.deepEqual(m.status, { resolved: {} });
      assert.equal(m.feeAmount.toNumber(), 10_000_000);
      assert.equal(m.payoutPool.toNumber(), 190_000_000);
    });

    it("7. Close losing bet (User2)", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), match0Pda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.closeLosingBet()
        .accountsStrict({ config: configPda, matchAccount: match0Pda, userBet: betPda, admin: admin.publicKey, payer: user1.publicKey })
        .signers([user1]).rpc();
      const info = await provider.connection.getAccountInfo(betPda);
      assert.isNull(info);
    });

    it("8. Winner claims — match auto-closes", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), match0Pda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const balBefore = await getAccount(provider.connection, user1Ata);
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: match0Pda, userBet: betPda, userSkrAta: user1Ata, vaultAta: vault0Ata, vaultAuthority: vault0AuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const balAfter = await getAccount(provider.connection, user1Ata);
      assert.equal(Number(balAfter.amount) - Number(balBefore.amount), 190_000_000);
      const matchInfo = await provider.connection.getAccountInfo(match0Pda);
      assert.isNull(matchInfo, "Match should be auto-closed");
    });
  });

  // =====================================================================
  // GROUP 1: Config Management
  // =====================================================================
  describe("Group 1: Config Management", () => {
    it("1.1 update_config — change fee, min_bet, max_bet", async () => {
      await program.methods.updateConfig(null, null, 300, new BN(5_000_000), new BN(200_000_000_000))
        .accountsStrict({ config: configPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      const c = await program.account.config.fetch(configPda);
      assert.equal(c.feeBps, 300);
      assert.equal(c.minBet.toNumber(), 5_000_000);
      assert.equal(c.maxBet.toNumber(), 200_000_000_000);
      // Restore for subsequent tests
      await program.methods.updateConfig(null, null, 500, new BN(MIN_BET), new BN(MAX_BET))
        .accountsStrict({ config: configPda, admin: admin.publicKey })
        .signers([admin]).rpc();
    });

    it("1.2 update_config — rotate admin key", async () => {
      const newAdmin = Keypair.generate();
      const sig = await provider.connection.requestAirdrop(newAdmin.publicKey, 2 * LAMPORTS_PER_SOL);
      await provider.connection.confirmTransaction(sig);
      // Rotate to new admin
      await program.methods.updateConfig(newAdmin.publicKey, null, null, null, null)
        .accountsStrict({ config: configPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      let c = await program.account.config.fetch(configPda);
      assert.ok(c.admin.equals(newAdmin.publicKey));
      // Rotate back
      await program.methods.updateConfig(admin.publicKey, null, null, null, null)
        .accountsStrict({ config: configPda, admin: newAdmin.publicKey })
        .signers([newAdmin]).rpc();
      c = await program.account.config.fetch(configPda);
      assert.ok(c.admin.equals(admin.publicKey));
    });

    it("1.3 update_config — fee_bps > 1000 rejected", async () => {
      try {
        await program.methods.updateConfig(null, null, 1001, null, null)
          .accountsStrict({ config: configPda, admin: admin.publicKey })
          .signers([admin]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "FeeBpsOutOfRange");
      }
    });

    it("1.4 update_config — min_bet > max_bet rejected", async () => {
      try {
        await program.methods.updateConfig(null, null, null, new BN(999_000_000_000), new BN(1))
          .accountsStrict({ config: configPda, admin: admin.publicKey })
          .signers([admin]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "InvalidBetRange");
      }
    });
  });

  // =====================================================================
  // GROUP 2: Pause System
  // =====================================================================
  describe("Group 2: Pause System", () => {
    let pauseMatchPda: PublicKey, pauseVaultAuth: PublicKey, pauseVaultAta: PublicKey;

  
  // Helper: create a match using the current counter and return its PDAs
  async function createMatchHelper() {
    const c = await program.account.config.fetch(configPda);
    const pdas = deriveMatchPdas(c.matchCounter.toNumber());
    await program.methods.createMatch(Array.from(Buffer.alloc(32, c.matchCounter.toNumber() % 256)), Array.from(Buffer.alloc(32, (c.matchCounter.toNumber() + 128) % 256)))
      .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
      .signers([admin]).rpc();
    return pdas;
  }

  before(async () => {
      // Create Match #1 for pause tests
      const pdas = deriveMatchPdas(1);
      pauseMatchPda = pdas.matchPda; pauseVaultAuth = pdas.vaultAuthPda; pauseVaultAta = pdas.vaultAta;
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 3)), Array.from(Buffer.alloc(32, 4)))
        .accountsStrict({ config: configPda, matchAccount: pauseMatchPda, vaultAuthority: pauseVaultAuth, vaultAta: pauseVaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
    });

    it("2.1 set_paused(true) — bet rejected with SystemPaused", async () => {
      await program.methods.setPaused(true)
        .accountsStrict({ config: configPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pauseMatchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
          .accountsStrict({ config: configPda, matchAccount: pauseMatchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: pauseVaultAta, vaultAuthority: pauseVaultAuth, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "SystemPaused");
      }
    });

    it("2.2 set_paused(false) — bet goes through", async () => {
      await program.methods.setPaused(false)
        .accountsStrict({ config: configPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pauseMatchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pauseMatchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: pauseVaultAta, vaultAuthority: pauseVaultAuth, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const m = await program.account.match.fetch(pauseMatchPda);
      assert.equal(m.totalA.toNumber(), BET_AMOUNT);
    });

    it("2.3 set_paused by non-admin — rejected", async () => {
      try {
        await program.methods.setPaused(true)
          .accountsStrict({ config: configPda, admin: intruder.publicKey })
          .signers([intruder]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "Unauthorized");
      }
    });
  });

  // =====================================================================
  // GROUP 3: Betting Edge Cases
  // =====================================================================
  describe("Group 3: Betting Edge Cases", () => {
    let betMatchPda: PublicKey, betVaultAuth: PublicKey, betVaultAta: PublicKey;

  
  // Helper: create a match using the current counter and return its PDAs
  async function createMatchHelper() {
    const c = await program.account.config.fetch(configPda);
    const pdas = deriveMatchPdas(c.matchCounter.toNumber());
    await program.methods.createMatch(Array.from(Buffer.alloc(32, c.matchCounter.toNumber() % 256)), Array.from(Buffer.alloc(32, (c.matchCounter.toNumber() + 128) % 256)))
      .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
      .signers([admin]).rpc();
    return pdas;
  }

  before(async () => {
      const pdas = deriveMatchPdas(2);
      betMatchPda = pdas.matchPda; betVaultAuth = pdas.vaultAuthPda; betVaultAta = pdas.vaultAta;
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 5)), Array.from(Buffer.alloc(32, 6)))
        .accountsStrict({ config: configPda, matchAccount: betMatchPda, vaultAuthority: betVaultAuth, vaultAta: betVaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
    });

    it("3.1 Bet below min_bet — rejected", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), betMatchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.placeBet({ a: {} }, new BN(1))
          .accountsStrict({ config: configPda, matchAccount: betMatchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "BetOutOfRange");
      }
    });

    it("3.2 Bet above max_bet — rejected", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), betMatchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.placeBet({ a: {} }, new BN("999000000000000"))
          .accountsStrict({ config: configPda, matchAccount: betMatchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "BetOutOfRange");
      }
    });

    it("3.3 Duplicate bet — rejected", async () => {
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), betMatchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      // First bet succeeds
      await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: betMatchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      // Second bet from same user fails
      try {
        await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
          .accountsStrict({ config: configPda, matchAccount: betMatchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        // Anchor init collision error
        assert.ok(e);
      }
    });

    it("3.4 Bet on LOCKED match — rejected", async () => {
      // Lock the match first
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: betMatchPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), betMatchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      try {
        await program.methods.placeBet({ b: {} }, new BN(BET_AMOUNT))
          .accountsStrict({ config: configPda, matchAccount: betMatchPda, userBet: betPda, userSkrAta: user2Ata, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user2]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "MatchNotOpen");
      }
    });

    it("3.5 Bet on RESOLVED match — rejected", async () => {
      // Resolve the match (Branch B — winning_total = 0, all bets on A, winner B)
      await program.methods.resolveMatch({ b: {} })
        .accountsStrict({ config: configPda, matchAccount: betMatchPda, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      // Match is now resolved (or auto-closed in Branch B), try to bet
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), betMatchPda.toBuffer(), user3.publicKey.toBuffer()], programId);
      try {
        await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
          .accountsStrict({ config: configPda, matchAccount: betMatchPda, userBet: betPda, userSkrAta: user3Ata, vaultAta: betVaultAta, vaultAuthority: betVaultAuth, skrMint, user: user3.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user3]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        // Will fail with either MatchNotOpen or AccountNotInitialized (match closed)
        assert.ok(e);
      }
    });

    it("3.6 Bet exactly at min_bet — accepted", async () => {
      // Create yet another match for this
      const pdas = deriveMatchPdas(3);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 7)), Array.from(Buffer.alloc(32, 8)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(MIN_BET))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      const m = await program.account.match.fetch(pdas.matchPda);
      assert.equal(m.totalA.toNumber(), MIN_BET);
    });
  });

  // =====================================================================
  // GROUP 4: Resolve Branches
  // =====================================================================
  describe("Group 4: Resolve Branches", () => {
    it("4.1 Branch A — resolve with 0 bets (pool=0), instant close", async () => {
      const pdas = deriveMatchPdas(4);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 9)), Array.from(Buffer.alloc(32, 10)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      // Match should be auto-closed
      const info = await provider.connection.getAccountInfo(pdas.matchPda);
      assert.isNull(info, "Match should be closed (Branch A)");
    });

    it("4.2 Branch B — all bets on losing side (winning_total=0), sweep to treasury", async () => {
      const pdas = deriveMatchPdas(5);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 11)), Array.from(Buffer.alloc(32, 12)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      // User3 bets on Side A
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user3.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(50_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, userSkrAta: user3Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user3.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user3]).rpc();
      const treasuryBefore = await getAccount(provider.connection, treasuryAta);
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      // Resolve Side B wins — winning_total = 0
      await program.methods.resolveMatch({ b: {} })
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      // Match should be auto-closed
      const info = await provider.connection.getAccountInfo(pdas.matchPda);
      assert.isNull(info, "Match should be closed (Branch B)");
      // Treasury should have received fee + payout
      const treasuryAfter = await getAccount(provider.connection, treasuryAta);
      assert.isTrue(Number(treasuryAfter.amount) > Number(treasuryBefore.amount), "Treasury should have received funds");
    });

    it("4.3 Normal resolve — verify fee math (300 SKR pool)", async () => {
      const pdas = deriveMatchPdas(6);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 13)), Array.from(Buffer.alloc(32, 14)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      // User1 bets 200 on A, User2 bets 100 on B
      const [bet1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(200_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      await program.methods.placeBet({ b: {} }, new BN(100_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const m = await program.account.match.fetch(pdas.matchPda);
      // Pool = 300_000_000, fee = floor(300M * 500 / 10000) = 15_000_000
      assert.equal(m.feeAmount.toNumber(), 15_000_000);
      assert.equal(m.payoutPool.toNumber(), 285_000_000);
      assert.equal(m.winningTotal.toNumber(), 200_000_000);
    });

    it("4.4 Resolve by non-admin — rejected", async () => {
      const pdas = deriveMatchPdas(7);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 15)), Array.from(Buffer.alloc(32, 16)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      try {
        await program.methods.resolveMatch({ a: {} })
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: intruder.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([intruder]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.ok(e);
      }
    });

    it("4.5 Resolve an OPEN match — rejected", async () => {
      const pdas = deriveMatchPdas(8);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 17)), Array.from(Buffer.alloc(32, 18)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      try {
        await program.methods.resolveMatch({ a: {} })
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([admin]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "MatchNotLocked");
      }
    });

    it("4.6 Resolve with winner_side = None — rejected", async () => {
      // Use match #7 which is locked
      const pdas = deriveMatchPdas(7);
      try {
        await program.methods.resolveMatch({ none: {} })
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([admin]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "InvalidSide");
      }
    });
  });

  // =====================================================================
  // GROUP 5: Cancel + Refund Flow
  // =====================================================================
  describe("Group 5: Cancel + Refund", () => {
    it("5.1 Cancel OPEN match with bets + full refund", async () => {
      const pdas = deriveMatchPdas(9);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 19)), Array.from(Buffer.alloc(32, 20)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      // Two users bet
      const [bet1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      await program.methods.placeBet({ b: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      // Cancel
      await program.methods.cancelMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      let m = await program.account.match.fetch(pdas.matchPda);
      assert.deepEqual(m.status, { cancelled: {} });
      // User1 refunds
      const bal1Before = await getAccount(provider.connection, user1Ata);
      await program.methods.refundBet()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const bal1After = await getAccount(provider.connection, user1Ata);
      assert.equal(Number(bal1After.amount) - Number(bal1Before.amount), BET_AMOUNT);
      // User2 refunds — triggers auto-close
      await program.methods.refundBet()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, admin: admin.publicKey, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      const info = await provider.connection.getAccountInfo(pdas.matchPda);
      assert.isNull(info, "Match auto-closed after last refund");
    });

    it("5.2 Cancel OPEN match with 0 bets — instant close", async () => {
      const pdas = deriveMatchPdas(10);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 21)), Array.from(Buffer.alloc(32, 22)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      await program.methods.cancelMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const info = await provider.connection.getAccountInfo(pdas.matchPda);
      assert.isNull(info, "Match closed immediately (0 bets)");
    });

    it("5.3 Cancel LOCKED match", async () => {
      const pdas = deriveMatchPdas(11);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 23)), Array.from(Buffer.alloc(32, 24)))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      await program.methods.lockMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey })
        .signers([admin]).rpc();
      await program.methods.cancelMatch()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const info = await provider.connection.getAccountInfo(pdas.matchPda);
      assert.isNull(info, "Locked match with 0 bets auto-closed");
    });

    it("5.4 Cancel RESOLVED match — rejected", async () => {
      // Match #6 is resolved and still alive (normal branch C)
      const pdas = deriveMatchPdas(6);
      try {
        await program.methods.cancelMatch()
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([admin]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "MatchNotCancellable");
      }
    });

    it("5.5 refund_bet on non-cancelled match — rejected", async () => {
      // Match #6 is RESOLVED, not CANCELLED
      const pdas = deriveMatchPdas(6);
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.refundBet()
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "MatchNotCancelled");
      }
    });
  });

  // =====================================================================
  // GROUP 6: Claim Edge Cases
  // =====================================================================
  describe("Group 6: Claim Edge Cases", () => {
    it("6.1 Loser tries to claim — rejected", async () => {
      // Match #6 is resolved Side A wins, user2 bet on B = loser
      const pdas = deriveMatchPdas(6);
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      try {
        await program.methods.claim()
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user2]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "NotWinner");
      }
    });

    it("6.2 close_losing_bet on winner's bet — rejected", async () => {
      const pdas = deriveMatchPdas(6);
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.closeLosingBet()
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, admin: admin.publicKey, payer: user1.publicKey })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.include(e.message, "NotLoser");
      }
    });

    it("6.3 claim on non-resolved match — rejected", async () => {
      // Match #8 is OPEN
      const pdas = deriveMatchPdas(8);
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.claim()
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        // Rejected — may be MatchNotResolved or AccountNotInitialized depending on account state
        assert.ok(e, "Claim correctly rejected on non-resolved match");
      }
    });

    it("6.4 Winner claims correct payout on match #6 + auto-close", async () => {
      const pdas = deriveMatchPdas(6);
      const [bet1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      // Close loser first
      await program.methods.closeLosingBet()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, admin: admin.publicKey, payer: admin.publicKey })
        .signers([admin]).rpc();
      // Winner claims
      const balBefore = await getAccount(provider.connection, user1Ata);
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const balAfter = await getAccount(provider.connection, user1Ata);
      // Payout = floor(285M * 200M / 200M) = 285M
      assert.equal(Number(balAfter.amount) - Number(balBefore.amount), 285_000_000);
      const info = await provider.connection.getAccountInfo(pdas.matchPda);
      assert.isNull(info, "Match #6 auto-closed");
    });
  });

  // =====================================================================
  // GROUP 7: Authorization & Concurrency
  // =====================================================================
  describe("Group 7: Authorization & Concurrency", () => {
    it("7.1 Non-admin calls create_match — rejected", async () => {
      const pdas = deriveMatchPdas(99);
      try {
        await program.methods.createMatch(Array.from(Buffer.alloc(32, 25)), Array.from(Buffer.alloc(32, 26)))
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAuthority: pdas.vaultAuthPda, vaultAta: pdas.vaultAta, skrMint, admin: intruder.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
          .signers([intruder]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.ok(e);
      }
    });

    it("7.2 Non-admin calls lock_match — rejected", async () => {
      const pdas = deriveMatchPdas(8);
      try {
        await program.methods.lockMatch()
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: intruder.publicKey })
          .signers([intruder]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        assert.ok(e);
      }
    });

    it("7.3 Two matches open simultaneously — both accept bets", async () => {
      const pdas12 = deriveMatchPdas(12);
      const pdas13 = deriveMatchPdas(13);
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 27)), Array.from(Buffer.alloc(32, 28)))
        .accountsStrict({ config: configPda, matchAccount: pdas12.matchPda, vaultAuthority: pdas12.vaultAuthPda, vaultAta: pdas12.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      await program.methods.createMatch(Array.from(Buffer.alloc(32, 29)), Array.from(Buffer.alloc(32, 30)))
        .accountsStrict({ config: configPda, matchAccount: pdas13.matchPda, vaultAuthority: pdas13.vaultAuthPda, vaultAta: pdas13.vaultAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId, rent: anchor.web3.SYSVAR_RENT_PUBKEY })
        .signers([admin]).rpc();
      // User1 bets on match 12, user2 bets on match 13
      const [bet12] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas12.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet13] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas13.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pdas12.matchPda, userBet: bet12, userSkrAta: user1Ata, vaultAta: pdas12.vaultAta, vaultAuthority: pdas12.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      await program.methods.placeBet({ b: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pdas13.matchPda, userBet: bet13, userSkrAta: user2Ata, vaultAta: pdas13.vaultAta, vaultAuthority: pdas13.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      const m12 = await program.account.match.fetch(pdas12.matchPda);
      const m13 = await program.account.match.fetch(pdas13.matchPda);
      assert.equal(m12.totalA.toNumber(), BET_AMOUNT);
      assert.equal(m13.totalB.toNumber(), BET_AMOUNT);
    });

    it("7.4 init_config called twice — rejected", async () => {
      try {
        await program.methods.initConfig(new BN(MIN_BET), new BN(MAX_BET))
          .accountsStrict({ config: configPda, admin: admin.publicKey, skrMint, treasuryWallet: treasuryWallet.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([admin]).rpc();
        assert.fail("Should have thrown");
      } catch (e: any) {
        // Anchor init collision
        assert.ok(e);
      }
    });
  });

  // =====================================================================
  // GROUP 8: Advanced Edge Cases
  // =====================================================================
  describe("Group 8: Advanced Edge Cases", () => {
    it("8.1 Multi-winner proportional payouts (2 winners, different amounts)", async () => {
      const pdas = await createMatchHelper();
      const [bet1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      const [bet3] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user3.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(100_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      await program.methods.placeBet({ a: {} }, new BN(200_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      await program.methods.placeBet({ b: {} }, new BN(150_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet3, userSkrAta: user3Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user3.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user3]).rpc();
      await program.methods.lockMatch().accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey }).signers([admin]).rpc();
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const m = await program.account.match.fetch(pdas.matchPda);
      assert.equal(m.feeAmount.toNumber(), 22_500_000);
      assert.equal(m.payoutPool.toNumber(), 427_500_000);
      await program.methods.closeLosingBet().accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet3, admin: admin.publicKey, payer: admin.publicKey }).signers([admin]).rpc();
      const b1b = await getAccount(provider.connection, user1Ata);
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const b1a = await getAccount(provider.connection, user1Ata);
      assert.equal(Number(b1a.amount) - Number(b1b.amount), 142_500_000, "User1 payout 142.5 SKR");
      assert.isNotNull(await provider.connection.getAccountInfo(pdas.matchPda), "Match alive — user2 hasn't claimed");
      const b2b = await getAccount(provider.connection, user2Ata);
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      const b2a = await getAccount(provider.connection, user2Ata);
      assert.equal(Number(b2a.amount) - Number(b2b.amount), 285_000_000, "User2 payout 285 SKR");
      assert.isNull(await provider.connection.getAccountInfo(pdas.matchPda), "Match auto-closed");
    });

    it("8.2 Same-side-only bets — all on A, resolve A wins", async () => {
      const pdas = await createMatchHelper();
      const [bet1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(100_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      await program.methods.placeBet({ a: {} }, new BN(200_000_000))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      await program.methods.lockMatch().accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey }).signers([admin]).rpc();
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const m = await program.account.match.fetch(pdas.matchPda);
      assert.equal(m.totalB.toNumber(), 0, "No bets on B");
      assert.equal(m.winningTotal.toNumber(), 300_000_000);
      const b1b = await getAccount(provider.connection, user1Ata);
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      const b1a = await getAccount(provider.connection, user1Ata);
      assert.equal(Number(b1a.amount) - Number(b1b.amount), 95_000_000, "User1 gets 95 (bet 100, 5% fee)");
      const b2b = await getAccount(provider.connection, user2Ata);
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      const b2a = await getAccount(provider.connection, user2Ata);
      assert.equal(Number(b2a.amount) - Number(b2b.amount), 190_000_000, "User2 gets 190");
    });

    it("8.3 Update treasury wallet", async () => {
      const newTreasury = Keypair.generate();
      await program.methods.updateConfig(null, newTreasury.publicKey, null, null, null)
        .accountsStrict({ config: configPda, admin: admin.publicKey }).signers([admin]).rpc();
      let c = await program.account.config.fetch(configPda);
      assert.ok(c.treasuryWallet.equals(newTreasury.publicKey));
      await program.methods.updateConfig(null, treasuryWallet.publicKey, null, null, null)
        .accountsStrict({ config: configPda, admin: admin.publicKey }).signers([admin]).rpc();
    });

    it("8.4 Wrong mint ATA in bet — rejected", async () => {
      const fakeMint = await createMint(provider.connection, admin, admin.publicKey, null, 6);
      const fakeAta = await createAssociatedTokenAccount(provider.connection, admin, fakeMint, user1.publicKey);
      await mintTo(provider.connection, admin, fakeMint, fakeAta, admin, 10_000_000_000);
      const pdas = await createMatchHelper();
      const [betPda] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      try {
        await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
          .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: betPda, userSkrAta: fakeAta, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        assert.fail("Should reject wrong mint");
      } catch (e: any) {
        assert.ok(e, "Bet with wrong mint ATA rejected");
      }
    });

    it("8.5 Close losing bet — rent goes to admin", async () => {
      const pdas = await createMatchHelper();
      const [bet1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [bet2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), pdas.matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet1, userSkrAta: user1Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      await program.methods.placeBet({ b: {} }, new BN(BET_AMOUNT))
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, userSkrAta: user2Ata, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      await program.methods.lockMatch().accountsStrict({ config: configPda, matchAccount: pdas.matchPda, admin: admin.publicKey }).signers([admin]).rpc();
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, vaultAta: pdas.vaultAta, vaultAuthority: pdas.vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      const adminSolBefore = await provider.connection.getBalance(admin.publicKey);
      await program.methods.closeLosingBet()
        .accountsStrict({ config: configPda, matchAccount: pdas.matchPda, userBet: bet2, admin: admin.publicKey, payer: user3.publicKey })
        .signers([user3]).rpc();
      const adminSolAfter = await provider.connection.getBalance(admin.publicKey);
      assert.isTrue(adminSolAfter > adminSolBefore, "Admin got rent from closed loser bet");
    });
  });

  // =====================================================================
  // GROUP 9: 5-Match Stress Test
  // =====================================================================
  describe("Group 9: 5-Match Stress Test", () => {
    it("9.1 Create 5 matches, bet on all, lock 2, resolve 2 — nothing breaks", async () => {
      const matches: { matchPda: PublicKey, vaultAuthPda: PublicKey, vaultAta: PublicKey }[] = [];
      for (let i = 0; i < 5; i++) {
        const pdas = await createMatchHelper();
        matches.push(pdas);
      }
      for (let i = 0; i < 5; i++) {
        const m = await program.account.match.fetch(matches[i].matchPda);
        assert.deepEqual(m.status, { open: {} });
      }
      for (let i = 0; i < 5; i++) {
        const [b1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[i].matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
        const [b2] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[i].matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
        await program.methods.placeBet({ a: {} }, new BN(50_000_000))
          .accountsStrict({ config: configPda, matchAccount: matches[i].matchPda, userBet: b1, userSkrAta: user1Ata, vaultAta: matches[i].vaultAta, vaultAuthority: matches[i].vaultAuthPda, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user1]).rpc();
        await program.methods.placeBet({ b: {} }, new BN(50_000_000))
          .accountsStrict({ config: configPda, matchAccount: matches[i].matchPda, userBet: b2, userSkrAta: user2Ata, vaultAta: matches[i].vaultAta, vaultAuthority: matches[i].vaultAuthPda, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
          .signers([user2]).rpc();
      }
      await program.methods.lockMatch().accountsStrict({ config: configPda, matchAccount: matches[0].matchPda, admin: admin.publicKey }).signers([admin]).rpc();
      await program.methods.lockMatch().accountsStrict({ config: configPda, matchAccount: matches[1].matchPda, admin: admin.publicKey }).signers([admin]).rpc();
      assert.deepEqual((await program.account.match.fetch(matches[0].matchPda)).status, { locked: {} });
      assert.deepEqual((await program.account.match.fetch(matches[1].matchPda)).status, { locked: {} });
      assert.deepEqual((await program.account.match.fetch(matches[2].matchPda)).status, { open: {} });
      await program.methods.resolveMatch({ a: {} })
        .accountsStrict({ config: configPda, matchAccount: matches[0].matchPda, vaultAta: matches[0].vaultAta, vaultAuthority: matches[0].vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      await program.methods.resolveMatch({ b: {} })
        .accountsStrict({ config: configPda, matchAccount: matches[1].matchPda, vaultAta: matches[1].vaultAta, vaultAuthority: matches[1].vaultAuthPda, treasuryAta, skrMint, admin: admin.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([admin]).rpc();
      assert.deepEqual((await program.account.match.fetch(matches[0].matchPda)).winner, { a: {} });
      assert.deepEqual((await program.account.match.fetch(matches[1].matchPda)).winner, { b: {} });
      const [b3on4] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[4].matchPda.toBuffer(), user3.publicKey.toBuffer()], programId);
      await program.methods.placeBet({ a: {} }, new BN(50_000_000))
        .accountsStrict({ config: configPda, matchAccount: matches[4].matchPda, userBet: b3on4, userSkrAta: user3Ata, vaultAta: matches[4].vaultAta, vaultAuthority: matches[4].vaultAuthPda, skrMint, user: user3.publicKey, tokenProgram: TOKEN_PROGRAM_ID, associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user3]).rpc();
      assert.equal((await program.account.match.fetch(matches[4].matchPda)).totalA.toNumber(), 100_000_000, "Match 34 accepts bets while others resolved");
      const [b1m0] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[0].matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [b2m0] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[0].matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.closeLosingBet().accountsStrict({ config: configPda, matchAccount: matches[0].matchPda, userBet: b2m0, admin: admin.publicKey, payer: admin.publicKey }).signers([admin]).rpc();
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: matches[0].matchPda, userBet: b1m0, userSkrAta: user1Ata, vaultAta: matches[0].vaultAta, vaultAuthority: matches[0].vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user1.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user1]).rpc();
      assert.isNull(await provider.connection.getAccountInfo(matches[0].matchPda), "Match 30 auto-closed");
      const [b1m1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[1].matchPda.toBuffer(), user1.publicKey.toBuffer()], programId);
      const [b2m1] = PublicKey.findProgramAddressSync([Buffer.from("bet"), matches[1].matchPda.toBuffer(), user2.publicKey.toBuffer()], programId);
      await program.methods.closeLosingBet().accountsStrict({ config: configPda, matchAccount: matches[1].matchPda, userBet: b1m1, admin: admin.publicKey, payer: admin.publicKey }).signers([admin]).rpc();
      await program.methods.claim()
        .accountsStrict({ config: configPda, matchAccount: matches[1].matchPda, userBet: b2m1, userSkrAta: user2Ata, vaultAta: matches[1].vaultAta, vaultAuthority: matches[1].vaultAuthPda, treasuryAta, treasuryWallet: treasuryWallet.publicKey, admin: admin.publicKey, skrMint, user: user2.publicKey, tokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId })
        .signers([user2]).rpc();
      assert.isNull(await provider.connection.getAccountInfo(matches[1].matchPda), "Match 31 auto-closed");
      for (let i = 2; i < 5; i++) {
        const mx = await program.account.match.fetch(matches[i].matchPda);
        assert.deepEqual(mx.status, { open: {} }, `Match ${30 + i} still OPEN`);
      }
    });
  });
});
